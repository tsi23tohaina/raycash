// raycash_complete.ino -- firmware merge :
//   - Capteurs HC-SR04 (proximite) + IR (bac plein) + LCD I2C (du nouveau sketch)
//   - Securite X-API-Key + HMAC-SHA256 + NTP (compat server/.env actuel)
//   - Robustesse : timeout Wi-Fi, reconnexion auto, etat machine non-bloquant,
//                  timeout du verrou si serveur ne repond pas
//
// PRE-REQUIS (Arduino IDE / Manage Libraries) :
//   - ESP32Servo (Kevin Harrington)
//   - LiquidCrystal_I2C (Frank de Brabander / Marco Schwartz)
//   - WiFi, HTTPClient, WebServer, Wire : fournis par le board package esp32
//   - mbedtls/md.h : fourni par le board package esp32 (HMAC-SHA256 hardware)
//
// CONFIG : copie secrets.h.example -> secrets.h et remplis.

#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <ESP32Servo.h>
#include <time.h>
#include "mbedtls/md.h"
#include "secrets.h"

// =============================================================================
//                         CONFIGURATION (pins inchangees)
// =============================================================================
const int PIN_BOUTON = 4;    // Interrupteur ON/OFF
const int PIN_BUZZER = 18;
const int PIN_TRIG   = 5;    // HC-SR04
const int PIN_ECHO   = 19;   // HC-SR04
const int PIN_IR     = 33;   // bac plein
const int PIN_SERVO  = 13;

const int ANGLE_REPOS      = 90;
const int ANGLE_RECYCLABLE = 180;
const int ANGLE_INCONNU    = 0;

const float DISTANCE_SEUIL_CM   = 10.0;
const unsigned long DEBOUNCE_MS = 50;
const unsigned long TRI_DUREE_MS         = 4000;  // duree position servo
const unsigned long TRI_RETOUR_MS        =  500;  // pause avant retour repos
const unsigned long VERROU_TIMEOUT_MS    = 15000; // libere verrou si serveur muet
const unsigned long WIFI_TIMEOUT_MS      = 20000; // setup : timeout Wi-Fi
const unsigned long WIFI_RECHECK_MS      =  5000; // loop : reconnexion auto
const unsigned long DISTANCE_INTERVAL_MS =  100;  // throttle HC-SR04
const unsigned long LCD_REFRESH_MS       =  500;

const char* NTP_SERVER = "pool.ntp.org";

// =============================================================================
//                                 OBJETS GLOBAUX
// =============================================================================
LiquidCrystal_I2C lcd(0x3F, 16, 2);  // si rien ne s'affiche : essayer 0x27
Servo triServo;
WebServer http(80);

// =============================================================================
//                                    ETATS
// =============================================================================
enum EtatTri { TRI_INACTIF, TRI_PIVOT, TRI_RETOUR };

bool systemeActive       = false;
bool verrouSignal        = false;
unsigned long verrouT0   = 0;

EtatTri etatTri          = TRI_INACTIF;
unsigned long triT0      = 0;

bool dernierEtatBouton   = HIGH;
unsigned long debounceT0 = 0;

unsigned long derniereLectureDist = 0;
unsigned long dernierRefreshLcd   = 0;
unsigned long derniereVerifWifi   = 0;

float distanceActuelle   = -1.0;
bool bacPlein            = false;

// =============================================================================
//                                  HMAC HELPERS
// =============================================================================
String toHex(const uint8_t* buf, size_t len) {
  static const char hex[] = "0123456789abcdef";
  String out;
  out.reserve(len * 2);
  for (size_t i = 0; i < len; i++) {
    out += hex[buf[i] >> 4];
    out += hex[buf[i] & 0x0F];
  }
  return out;
}

// Signature = hex(HMAC-SHA256(secret, "<timestamp>." + body))
// (correspond a server/security.py:HmacVerifier.verify)
String hmacSign(const String& timestamp, const String& body) {
  const char* secret = RAYCASH_HMAC_SECRET;
  if (secret == nullptr || strlen(secret) == 0) {
    return String("");  // HMAC desactive cote serveur
  }
  String message = timestamp + "." + body;
  uint8_t digest[32];

  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, (const uint8_t*)secret, strlen(secret));
  mbedtls_md_hmac_update(&ctx, (const uint8_t*)message.c_str(), message.length());
  mbedtls_md_hmac_finish(&ctx, digest);
  mbedtls_md_free(&ctx);

  return toHex(digest, sizeof(digest));
}

// =============================================================================
//                                  AFFICHAGE LCD
// =============================================================================
// LCD 16 colonnes : on padde pour ecraser les caracteres precedents.
void lcdLigne(uint8_t row, const String& texte) {
  char buf[17];
  snprintf(buf, sizeof(buf), "%-16s", texte.c_str());
  lcd.setCursor(0, row);
  lcd.print(buf);
}

void afficherEtatPrincipal() {
  if (!systemeActive) {
    lcdLigne(0, "RAYCASH OFF");
    lcdLigne(1, bacPlein ? "Statut: bac plein" : "Statut: bac OK");
    return;
  }
  if (etatTri != TRI_INACTIF) return;  // ne pas ecraser pendant le tri

  if (verrouSignal) {
    lcdLigne(0, "Analyse...");
    lcdLigne(1, "IA en cours");
    return;
  }
  if (distanceActuelle > 0 && distanceActuelle <= DISTANCE_SEUIL_CM) {
    char l1[17];
    snprintf(l1, sizeof(l1), "Dist: %.1f cm", distanceActuelle);
    lcdLigne(0, "Dechet detecte!");
    lcdLigne(1, l1);
    return;
  }
  lcdLigne(0, "RayCash: pret");
  lcdLigne(1, "Deposez un objet");
}

// =============================================================================
//                                  RESEAU
// =============================================================================
bool wifiConnect(unsigned long timeoutMs) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - t0) < timeoutMs) {
    delay(250);
    Serial.print('.');
  }
  return WiFi.status() == WL_CONNECTED;
}

void verifierWifi() {
  if (millis() - derniereVerifWifi < WIFI_RECHECK_MS) return;
  derniereVerifWifi = millis();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WIFI] perdu, reconnexion...");
    WiFi.reconnect();
  }
}

void syncNtp() {
  configTime(0, 0, NTP_SERVER);
  time_t now = 0;
  unsigned long t0 = millis();
  while (now < 1700000000 && (millis() - t0) < 5000) {
    delay(200);
    time(&now);
  }
  Serial.printf("[NTP] heure synchro : %ld\n", (long)now);
}

void envoyerSignalAServeur(const String& action) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[POST] Wi-Fi off, abandon");
    return;
  }
  HTTPClient client;
  client.begin(SERVER_URL);
  client.setTimeout(3000);
  client.addHeader("Content-Type", "application/json");
  client.addHeader("X-API-Key", RAYCASH_API_KEY);

  String body = String("{\"action\":\"") + action + "\"}";
  String ts = String((unsigned long)time(nullptr));
  String sig = hmacSign(ts, body);
  if (sig.length() > 0) {
    client.addHeader("X-Signature-Timestamp", ts);
    client.addHeader("X-Signature", sig);
  }

  int code = client.POST(body);
  Serial.printf("[POST] /esp_signal action=%s -> %d\n", action.c_str(), code);
  client.end();
}

// =============================================================================
//                                  CAPTEURS
// =============================================================================
float mesurerDistanceCm() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  long duree = pulseIn(PIN_ECHO, HIGH, 25000);  // 25 ms = ~4 m
  if (duree == 0) return -1.0;
  return duree * 0.034 / 2.0;
}

// =============================================================================
//                                  BUZZER
// =============================================================================
void beep(int freq, int dureeMs) { tone(PIN_BUZZER, freq, dureeMs); }

// =============================================================================
//                              ROUTES SERVEUR LOCAL
// =============================================================================
// Le serveur Flask appelle POST {ESP32_IP}/servo avec body = "RECYCLABLE" | "INCONNU"
// (cf. server/main.py:341). Header X-API-Key obligatoire.
void handleServo() {
  http.collectHeaders((const char*[]){"X-API-Key"}, 1);
  if (!http.hasHeader("X-API-Key") ||
      http.header("X-API-Key") != String(RAYCASH_API_KEY)) {
    http.send(401, "application/json", "{\"error\":\"unauthorized\"}");
    return;
  }
  if (!systemeActive) {
    http.send(200, "application/json", "{\"status\":\"ignored_system_off\"}");
    return;
  }
  String body = http.arg("plain");
  body.trim();
  Serial.printf("[/servo] ordre = %s\n", body.c_str());

  if (body == "RECYCLABLE") {
    beep(2000, 120);
    triServo.write(ANGLE_RECYCLABLE);
    lcd.clear();
    lcdLigne(0, "Dechet accepte!");
    lcdLigne(1, "Ticket en cours");
    etatTri = TRI_PIVOT;
    triT0 = millis();
  } else if (body == "INCONNU") {
    beep(350, 600);
    triServo.write(ANGLE_INCONNU);
    lcd.clear();
    lcdLigne(0, "Dechet inconnu");
    lcdLigne(1, "Pas de ticket");
    etatTri = TRI_PIVOT;
    triT0 = millis();
  } else {
    http.send(400, "application/json", "{\"error\":\"action_invalide\"}");
    return;
  }
  http.send(200, "application/json", "{\"status\":\"ok\"}");
}

void handleHealth() {
  http.send(200, "application/json", "{\"status\":\"ok\"}");
}

// =============================================================================
//                              MACHINE A ETATS TRI
// =============================================================================
// Evite tout delay() bloquant : le webserver continue d'accepter des requetes,
// le bouton ON/OFF reste reactif, le LCD reste fluide.
void avancerEtatTri() {
  if (etatTri == TRI_INACTIF) return;
  unsigned long maintenant = millis();
  if (etatTri == TRI_PIVOT && maintenant - triT0 >= TRI_DUREE_MS) {
    triServo.write(ANGLE_REPOS);
    etatTri = TRI_RETOUR;
    triT0 = maintenant;
  } else if (etatTri == TRI_RETOUR && maintenant - triT0 >= TRI_RETOUR_MS) {
    etatTri = TRI_INACTIF;
    verrouSignal = false;  // libere : pret pour un autre dechet
    lcd.clear();
  }
}

// =============================================================================
//                                  SETUP / LOOP
// =============================================================================
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== RayCash firmware complete ===");

  pinMode(PIN_BOUTON, INPUT_PULLUP);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_IR, INPUT_PULLUP);

  ESP32PWM::allocateTimer(0);
  triServo.setPeriodHertz(50);
  triServo.attach(PIN_SERVO, 500, 2400);
  triServo.write(ANGLE_REPOS);

  Wire.begin();
  lcd.init();
  lcd.backlight();
  lcdLigne(0, "RayCash boot...");
  lcdLigne(1, "Wi-Fi en cours");

  Serial.printf("[WIFI] connexion a %s ", WIFI_SSID);
  if (!wifiConnect(WIFI_TIMEOUT_MS)) {
    Serial.println("\n[WIFI] echec, redemarrage dans 3s");
    lcdLigne(0, "WIFI echec");
    lcdLigne(1, "Reboot...");
    delay(3000);
    ESP.restart();
  }
  Serial.printf("\n[WIFI] OK, IP = %s\n", WiFi.localIP().toString().c_str());
  lcdLigne(0, "Wi-Fi connecte");
  lcdLigne(1, WiFi.localIP().toString());

  syncNtp();

  http.on("/servo",  HTTP_POST, handleServo);
  http.on("/health", HTTP_GET,  handleHealth);
  http.onNotFound([]() {
    http.send(404, "application/json", "{\"error\":\"not_found\"}");
  });
  http.begin();
  Serial.println("[HTTP] serveur local pret");
  delay(800);
  lcd.clear();
}

void loop() {
  http.handleClient();
  verifierWifi();
  avancerEtatTri();

  // --- Bouton ON/OFF ---
  int lecture = digitalRead(PIN_BOUTON);
  if (lecture == LOW && dernierEtatBouton == HIGH &&
      (millis() - debounceT0) > DEBOUNCE_MS) {
    debounceT0 = millis();
    systemeActive = !systemeActive;
    verrouSignal = false;
    etatTri = TRI_INACTIF;
    triServo.write(ANGLE_REPOS);
    lcd.clear();
    Serial.println(systemeActive ? "[BTN] ACTIVE" : "[BTN] VEILLE");
    beep(systemeActive ? 1000 : 500, 200);
  }
  dernierEtatBouton = lecture;

  // --- IR (bac plein) -- pas critique, juste de l'info ---
  bacPlein = (digitalRead(PIN_IR) == LOW);

  // --- HC-SR04 (proximite) ---
  if (systemeActive && etatTri == TRI_INACTIF &&
      millis() - derniereLectureDist >= DISTANCE_INTERVAL_MS) {
    derniereLectureDist = millis();
    distanceActuelle = mesurerDistanceCm();

    if (distanceActuelle > 0 && distanceActuelle <= DISTANCE_SEUIL_CM &&
        !verrouSignal) {
      Serial.printf("[SR04] seuil atteint (%.1f cm) -> POST START\n", distanceActuelle);
      verrouSignal = true;
      verrouT0 = millis();
      envoyerSignalAServeur("START");
    }
  }

  // --- Timeout du verrou : si serveur ne repond jamais ---
  if (verrouSignal && etatTri == TRI_INACTIF &&
      millis() - verrouT0 > VERROU_TIMEOUT_MS) {
    Serial.println("[LOCK] timeout, libere verrou");
    verrouSignal = false;
    beep(400, 400);
  }

  // --- LCD : refresh throttled ---
  if (millis() - dernierRefreshLcd >= LCD_REFRESH_MS) {
    dernierRefreshLcd = millis();
    afficherEtatPrincipal();
  }
}
