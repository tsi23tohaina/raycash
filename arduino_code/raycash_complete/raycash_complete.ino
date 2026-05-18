// raycash_complete.ino -- version stable avec validation temporelle ultrason
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
//                         CONFIGURATION
// =============================================================================
const int PIN_BOUTON = 4;
const int PIN_BUZZER = 18;
const int PIN_TRIG   = 5;
const int PIN_ECHO   = 19;
const int PIN_IR     = 33;
const int PIN_SERVO  = 13;

const int ANGLE_REPOS           = 90;
const int ANGLE_RECYCLABLE      = 0;
const int ANGLE_NON_RECYCLABLE  = 180;

const float DISTANCE_SEUIL_CM   = 15.0;   // seuil de détection
const unsigned long DEBOUNCE_MS = 50;
const unsigned long TRI_DUREE_MS         = 3000;  // 3s : pivot servo + affichage resultat scan
const unsigned long TRI_RETOUR_MS        = 300;   // retour servo au repos
const unsigned long TRI_TOTAL_MS         = 3000;  // 3s : affichage point total + gain ar
const unsigned long VERROU_TIMEOUT_MS    = 15000;
const unsigned long WIFI_TIMEOUT_MS      = 20000;
const unsigned long WIFI_RECHECK_MS      = 5000;
const unsigned long DISTANCE_INTERVAL_MS = 100;   // intervalle entre mesures
const unsigned long LCD_REFRESH_MS       = 500;

// NOUVEAUX PARAMÈTRES TEMPORELS POUR L'ULTRASON
const unsigned long DELAI_CONFIRMATION_MS = 300;   // temps que l'objet doit rester sous seuil
const unsigned long DELAI_REARMEMENT_MS   = 500;   // temps d'absence avant réarmement

const char* NTP_SERVER = "pool.ntp.org";

// =============================================================================
//                                 OBJETS GLOBAUX
// =============================================================================
// LCD : allocation dynamique apres scan I2C. Couvre toutes les adresses
// PCF8574 (0x20..0x27) et PCF8574A (0x38..0x3F) des modules 16x2 generiques.
// `lcdReady` protege contre les calls quand le LCD n'est pas dispo.
LiquidCrystal_I2C* lcd = nullptr;
bool lcdReady = false;

Servo triServo;
WebServer http(80);

// =============================================================================
//                                    ETATS
// =============================================================================
// Etats du cycle de tri :
//   TRI_INACTIF  -> au repos, attente d'un nouveau dechet
//   TRI_PIVOT    -> servo bouge vers la categorie + LCD affiche resultat scan
//   TRI_RETOUR   -> servo revient au repos (LCD garde le resultat affiche)
//   TRI_TOTAL    -> LCD affiche le total session (points + gain ar)
enum EtatTri { TRI_INACTIF, TRI_PIVOT, TRI_RETOUR, TRI_TOTAL };

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
unsigned long derniereReSynchNtp  = 0;

float distanceActuelle   = -1.0;
bool bacPlein            = false;
int  pointsRecyclable    = 0;     // points du dernier scan
int  pointsSessionTotal  = 0;     // cumul depuis le boot/dernier reset

// Variables pour la validation temporelle
bool objetSousSeuil      = false;      // vrai si la distance brute est sous seuil
unsigned long debutPresence = 0;       // moment où l'objet est passé sous seuil
bool objetConfirme        = false;      // après DELAI_CONFIRMATION_MS
bool attenteLiberation    = false;      // après déclenchement, on attend que l'objet parte
unsigned long debutAbsence = 0;         // moment où la distance repasse au-dessus du seuil

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

String hmacSign(const String& timestamp, const String& body) {
  const char* secret = RAYCASH_HMAC_SECRET;
  if (secret == nullptr || strlen(secret) == 0) return String("");
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
// Helpers safe : ne crashent jamais si le LCD n'est pas init.
void lcdSafeClear() {
  if (!lcdReady || lcd == nullptr) return;
  lcd->clear();
  delay(2);  // certains clones ont besoin d'une marge apres clear
}

void lcdLigne(uint8_t row, const String& texte) {
  if (!lcdReady || lcd == nullptr) return;  // pas de LCD, on ne crash pas
  char buf[17];
  snprintf(buf, sizeof(buf), "%-16s", texte.c_str());
  lcd->setCursor(0, row);
  lcd->print(buf);
}

// Scan toute la plage commune PCF8574/PCF8574A et alloue dynamiquement le LCD
// a la premiere adresse qui ACK. Si aucun ACK (clones qui refusent le scan
// mais marchent quand meme), on tente 0x27 en aveugle. Init avec gros delays
// pour gerer les modules lents.
void initLcd() {
  const uint8_t candidates[] = {
    0x27, 0x3F,                              // les 2 plus communes
    0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, // PCF8574 (0x20-0x27)
    0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3D, 0x3E  // PCF8574A (0x38-0x3F)
  };
  uint8_t foundAddr = 0;

  Serial.println("[LCD] scan I2C...");
  for (uint8_t addr : candidates) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("[LCD]   device I2C a 0x%02X\n", addr);
      if (foundAddr == 0) foundAddr = addr;
    }
  }

  if (foundAddr == 0) {
    Serial.println("[LCD] aucun ACK. Tentative aveugle 0x27 (modules clones).");
    foundAddr = 0x27;
  }

  Serial.printf("[LCD] init sur 0x%02X\n", foundAddr);
  lcd = new LiquidCrystal_I2C(foundAddr, 16, 2);
  if (lcd == nullptr) {
    Serial.println("[LCD] echec alloc memoire");
    return;
  }

  // Sequence d'init robuste : sleep entre chaque etape pour gerer les clones
  // qui mettent du temps a se reveiller apres le power-up.
  delay(50);
  lcd->init();
  delay(150);
  lcd->backlight();
  delay(50);
  lcd->clear();
  delay(50);
  lcd->home();
  lcdReady = true;

  // Test visuel : affiche un pattern reconnaissable pendant 800ms pour
  // confirmer que l'ecran fonctionne avant d'enchainer le boot normal.
  lcd->setCursor(0, 0);
  lcd->print("LCD OK 0x");
  lcd->print(foundAddr, HEX);
  lcd->setCursor(0, 1);
  lcd->print("RayCash demarre");
  delay(800);
  lcd->clear();
}

void afficherEtatPrincipal() {
  if (!systemeActive) {
    lcdLigne(0, "RAYCASH OFF");
    lcdLigne(1, bacPlein ? "Statut: bac plein" : "Statut: bac OK");
    return;
  }
  if (etatTri != TRI_INACTIF) return;

  if (verrouSignal) {
    lcdLigne(0, "Analyse...");
    lcdLigne(1, "IA en cours");
    return;
  }
  if (objetConfirme) {
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
  if (now < 1700000000) {
    Serial.println("[NTP] attention : heure non synchronisée");
  } else {
    Serial.printf("[NTP] heure synchro : %ld\n", (long)now);
  }
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
//                                  CAPTEUR ULTRASON
// =============================================================================
float mesurerDistanceCm() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  long duree = pulseIn(PIN_ECHO, HIGH, 25000);
  if (duree == 0) return -1.0;
  return duree * 0.034 / 2.0;
}

void gererDetection() {
  if (!systemeActive || etatTri != TRI_INACTIF) return;

  // Lecture brute
  float dist = mesurerDistanceCm();
  if (dist > 0 && dist < 400) {
    distanceActuelle = dist;   // mise à jour pour affichage
  } else {
    distanceActuelle = -1.0;
  }

  bool zoneOccupee = (distanceActuelle > 0 && distanceActuelle <= DISTANCE_SEUIL_CM);
  bool zoneLibre   = (distanceActuelle < 0 || distanceActuelle > DISTANCE_SEUIL_CM);

  // Détection des transitions
  if (zoneOccupee && !objetSousSeuil) {
    // L'objet vient d'apparaître sous le seuil
    objetSousSeuil = true;
    debutPresence = millis();
    objetConfirme = false;
    Serial.printf("[SR04] objet détecté (%.1f cm)\n", distanceActuelle);
  }
  else if (!zoneOccupee && objetSousSeuil) {
    // L'objet a disparu avant confirmation
    objetSousSeuil = false;
    debutPresence = 0;
    objetConfirme = false;
    Serial.println("[SR04] objet parti avant confirmation");
  }

  // Confirmation si l'objet reste sous seuil pendant DELAI_CONFIRMATION_MS
  if (objetSousSeuil && !objetConfirme && (millis() - debutPresence >= DELAI_CONFIRMATION_MS)) {
    objetConfirme = true;
    Serial.printf("[SR04] objet confirmé (%.1f cm)\n", distanceActuelle);
  }

  // Déclenchement du START
  if (objetConfirme && !verrouSignal && !attenteLiberation) {
    Serial.println("[SR04] déclenchement START");
    verrouSignal = true;
    verrouT0 = millis();
    envoyerSignalAServeur("START");
    objetConfirme = false;
    objetSousSeuil = false;
    attenteLiberation = true;
    debutAbsence = 0;
  }

  // Gestion de l'absence durable après un déclenchement
  if (attenteLiberation && zoneLibre) {
    if (debutAbsence == 0) debutAbsence = millis();
    else if (millis() - debutAbsence >= DELAI_REARMEMENT_MS) {
      attenteLiberation = false;
      debutAbsence = 0;
      Serial.println("[SR04] zone libre, réarmement");
    }
  } else if (attenteLiberation && !zoneLibre) {
    debutAbsence = 0;   // l'objet est encore là, reset
  }
}

// =============================================================================
//                                  BUZZER (sans conflit avec servo)
// =============================================================================
// On utilise la bibliothèque standard tone() mais attention : elle peut
// interférer avec le timer du servo. Pour éviter cela, on désactive le servo
// pendant le bip, ou on utilise ledc. Version simple : on laisse tone() mais
// on évite de bip pendant le mouvement du servo.
void beep(int freq, int dureeMs) {
  tone(PIN_BUZZER, freq, dureeMs);
  delay(dureeMs);
  noTone(PIN_BUZZER);
}

// =============================================================================
//                              ROUTES SERVEUR LOCAL
// =============================================================================
void handleServo() {
  http.collectHeaders((const char*[]){"X-API-Key"}, 1);
  if (!http.hasHeader("X-API-Key") || http.header("X-API-Key") != String(RAYCASH_API_KEY)) {
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

  // Format attendu : "TRI_STATUS:points:label" (ex: "RECYCLABLE:40:Plastique").
  // Backward compat : si pas de 2eme ":", le label est vide.
  String triStatus = body;
  int pts = 0;
  String label = "";
  int sep1 = body.indexOf(':');
  if (sep1 > 0) {
    triStatus = body.substring(0, sep1);
    int sep2 = body.indexOf(':', sep1 + 1);
    if (sep2 > 0) {
      pts = body.substring(sep1 + 1, sep2).toInt();
      label = body.substring(sep2 + 1);
    } else {
      pts = body.substring(sep1 + 1).toInt();
    }
  }

  if (triStatus == "RECYCLABLE") {
    pointsRecyclable = pts;
    pointsSessionTotal += pts;
    beep(2000, 120);
    triServo.write(ANGLE_RECYCLABLE);
    lcdSafeClear();
    // Ligne 1 : "Recyclable" (en clair). Ligne 2 : "<label> +<pts>" tronque
    // a 16 chars par lcdLigne.
    char l1[24];
    snprintf(l1, sizeof(l1), "%s +%d", label.c_str(), pts);
    lcdLigne(0, "Recyclable");
    lcdLigne(1, l1);
    etatTri = TRI_PIVOT;
    triT0 = millis();
  } else if (triStatus == "NON_RECYCLABLE") {
    pointsRecyclable = 0;
    beep(350, 600);
    triServo.write(ANGLE_NON_RECYCLABLE);
    lcdSafeClear();
    lcdLigne(0, "Non recyclable");
    lcdLigne(1, "+0 pts");
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
void avancerEtatTri() {
  if (etatTri == TRI_INACTIF) return;
  unsigned long maintenant = millis();

  // TRI_PIVOT (3s) : servo bouge, LCD garde le resultat du scan ("Recyclable
  // / Plastique +40"). Apres TRI_DUREE_MS, on rapatrie le servo et on passe
  // a TRI_RETOUR.
  if (etatTri == TRI_PIVOT && maintenant - triT0 >= TRI_DUREE_MS) {
    triServo.write(ANGLE_REPOS);
    etatTri = TRI_RETOUR;
    triT0 = maintenant;
  }
  // TRI_RETOUR (~300ms) : servo termine son retour. A la fin, on bascule
  // l'affichage sur le total session (TRI_TOTAL).
  else if (etatTri == TRI_RETOUR && maintenant - triT0 >= TRI_RETOUR_MS) {
    lcdSafeClear();
    char l0[24];
    char l1[24];
    snprintf(l0, sizeof(l0), "Total: %d pts", pointsSessionTotal);
    snprintf(l1, sizeof(l1), "Gain: %d ar", pointsSessionTotal * 100);
    lcdLigne(0, l0);
    lcdLigne(1, l1);
    etatTri = TRI_TOTAL;
    triT0 = maintenant;
  }
  // TRI_TOTAL (3s) : l'utilisateur lit son total + gain. Apres, on revient
  // au mode "pret a scanner" en liberant le verrou.
  else if (etatTri == TRI_TOTAL && maintenant - triT0 >= TRI_TOTAL_MS) {
    etatTri = TRI_INACTIF;
    verrouSignal = false;
    lcdSafeClear();
  }
}

// =============================================================================
//                                  SETUP
// =============================================================================
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== RayCash firmware (validation temporelle) ===");

  pinMode(PIN_BOUTON, INPUT_PULLUP);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_IR, INPUT_PULLUP);

  // Initialisation servo
  ESP32PWM::allocateTimer(0);
  triServo.setPeriodHertz(50);
  triServo.attach(PIN_SERVO, 1000, 2000);
  triServo.write(ANGLE_REPOS);

  Wire.begin();
  delay(100);   // marge pour que le LCD termine son auto-reset interne

  // Init LCD robuste : scan I2C large, allocation dynamique, sequence
  // d'init avec delays adaptes aux modules clones.
  initLcd();
  lcdLigne(0, "RayCash boot...");
  lcdLigne(1, "Wi-Fi...");

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
  derniereReSynchNtp = millis();

  http.on("/servo",  HTTP_POST, handleServo);
  http.on("/health", HTTP_GET,  handleHealth);
  http.onNotFound([]() {
    http.send(404, "application/json", "{\"error\":\"not_found\"}");
  });
  http.begin();
  Serial.println("[HTTP] serveur local pret");
  delay(800);
  lcdSafeClear();
  lcdLigne(0, "RayCash: pret");
  lcdLigne(1, "Deposez un objet");

  // Attendre 2 secondes pour stabiliser l'ultrason
  for (int i = 0; i < 20; i++) {
    mesurerDistanceCm();
    delay(100);
  }
  // Réinitialiser les flags pour éviter un faux déclenchement initial
  objetSousSeuil = false;
  objetConfirme = false;
  attenteLiberation = true;   // exige une libération avant premier déclenchement
  debutPresence = 0;
  debutAbsence = 0;
  distanceActuelle = -1.0;
}

// =============================================================================
//                                  LOOP
// =============================================================================
void loop() {
  http.handleClient();
  verifierWifi();

  if (time(nullptr) < 1700000000 && (millis() - derniereReSynchNtp > 3600000)) {
    derniereReSynchNtp = millis();
    syncNtp();
  }

  avancerEtatTri();

  // Bouton ON/OFF
  int lecture = digitalRead(PIN_BOUTON);
  if (lecture == LOW && dernierEtatBouton == HIGH && (millis() - debounceT0) > DEBOUNCE_MS) {
    debounceT0 = millis();
    systemeActive = !systemeActive;
    verrouSignal = false;
    etatTri = TRI_INACTIF;
    objetSousSeuil = false;
    objetConfirme = false;
    attenteLiberation = true;  // on attend une libération après réactivation
    debutPresence = 0;
    debutAbsence = 0;
    triServo.write(ANGLE_REPOS);
    lcdSafeClear();
    Serial.println(systemeActive ? "[BTN] ACTIVE" : "[BTN] VEILLE");
    beep(systemeActive ? 1000 : 500, 200);
  }
  dernierEtatBouton = lecture;

  bacPlein = (digitalRead(PIN_IR) == LOW);

  // Gestion ultrason : appel périodique
  static unsigned long lastMeasure = 0;
  if (millis() - lastMeasure >= DISTANCE_INTERVAL_MS) {
    lastMeasure = millis();
    gererDetection();   // cette fonction lit la distance et gère les timings
  }

  // Timeout verrou
  if (verrouSignal && etatTri == TRI_INACTIF && millis() - verrouT0 > VERROU_TIMEOUT_MS) {
    Serial.println("[LOCK] timeout, libere verrou");
    verrouSignal = false;
    attenteLiberation = true;
    debutAbsence = 0;
    objetConfirme = false;
    objetSousSeuil = false;
    beep(400, 400);
  }

  // Rafraîchissement LCD
  if (millis() - dernierRefreshLcd >= LCD_REFRESH_MS) {
    dernierRefreshLcd = millis();
    afficherEtatPrincipal();
  }
}
