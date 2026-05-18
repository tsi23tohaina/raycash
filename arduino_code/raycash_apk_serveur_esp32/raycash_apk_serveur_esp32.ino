#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <ESP32Servo.h>

// --- CONFIGURATION WI-FI ---
const char* ssid = "DESKTOP-1T7GA6N 9411";
const char* password = "sitraka12345";

// --- CONFIGURATION SERVEUR API (PC CENTRE) ---
const char* serverUrl = "http://192.168.137.1:5000/esp_signal"; 

// --- CONFIGURATION DES BROCHES ---
const int PIN_BOUTON = 4;    // Interrupteur ON/OFF général
const int PIN_BUZZER = 18;   // Buzzer
const int PIN_TRIG   = 5;    // HC-SR04 Trigger
const int PIN_ECHO   = 19;   // HC-SR04 Echo
const int PIN_IR     = 33;   // Capteur Infrarouge (Bac plein)
const int PIN_SERVO  = 13;   // Servo Moteur

// --- INITIALISATION DES OBJETS ---
LiquidCrystal_I2C lcd(0x3F, 16, 2); 
Servo triServo;
WebServer server(80); 

// --- VARIABLES DE CONFIGURATION ---
const int ANGLE_NORMAL = 0;   
const int ANGLE_TRI    = 180; 

bool systemeActive = false;    // Géré par l'interrupteur PIN 4
bool dernierEtatBouton = HIGH;
unsigned long dernierTempsDebounce = 0;
const unsigned long delaiDebounce = 50;

// Verrou réseau
bool signalEnvoyeAuPC = false; 

void setup() {
  Serial.begin(115200);

  WiFi.disconnect(true);
  delay(1000);

  pinMode(PIN_BOUTON, INPUT_PULLUP); 
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_IR, INPUT);

  triServo.attach(PIN_SERVO);
  triServo.write(ANGLE_NORMAL); 

  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("ReyCash Init...");

  WiFi.begin(ssid, password);
  Serial.print("Connexion au Wi-Fi ");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  Serial.println("\n✅ Wi-Fi connecté !");
  Serial.print("IP de l'ESP32 : ");
  Serial.println(WiFi.localIP());

  server.on("/action", HTTP_POST, handlePCAction);
  server.begin();
  Serial.println("🌐 Serveur d'écoute local ESP32 prêt.");

  lcd.clear();
}

void loop() {
  server.handleClient(); // Écoute asynchrone du PC (Prioritaire pour recevoir RECYCLABLE/INCONNU)
  
  // 1. Logique de l'interrupteur ON/OFF général (Déclenchement immédiat)
  int lectureBouton = digitalRead(PIN_BOUTON);
  if (lectureBouton == LOW && dernierEtatBouton == HIGH && (millis() - dernierTempsDebounce) > delaiDebounce) {
    dernierTempsDebounce = millis();
    systemeActive = !systemeActive; 
    signalEnvoyeAuPC = false; // Reset immédiat du verrou réseau
    
    lcd.clear();
    if (systemeActive) {
      Serial.println("📢 Machine ACTIVÉE.");
      tone(PIN_BUZZER, 1000, 150); 
    } else {
      Serial.println("💤 Machine en VEILLE FORCÉE.");
      tone(PIN_BUZZER, 500, 300);  
      triServo.write(ANGLE_NORMAL); // Sécurité : On referme immédiatement le servo
    }
  }
  dernierEtatBouton = lectureBouton;

  int etatIR = digitalRead(PIN_IR); 

  // 2. Traitement des capteurs uniquement SI la machine est active (ON)
  if (systemeActive) {
    float distance = mesurerDistance();

    // Affichage moniteur série pour contrôle technique
    Serial.print("Distance actuelle : ");
    if (distance > 0) {
      Serial.print(distance);
      Serial.println(" cm");
    } else {
      Serial.println("Hors de portée");
    }

    // Logique de détection critique <= 10 cm
    if (distance > 0 && distance <= 10.0) {
      
      // Affichage persistant pendant que l'objet reste devant
      lcd.setCursor(0, 0);
      lcd.print("DECHET DETECTER!"); 
      lcd.setCursor(0, 1);
      lcd.print("Dist: "); lcd.print(distance, 1); lcd.print(" cm    ");

      // Envoi UNIQUE au PC Centre
      if (!signalEnvoyeAuPC) {
        Serial.println("🎯 Seuil atteint ! Envoi unique de BUTTON_CLICK...");
        signalEnvoyeAuPC = true; 
        envoyerSignalAServeur("BUTTON_CLICK"); 
      }
    } 
    else {
      // Si l'objet est retiré ou éloigné, et qu'on n'attend pas de réponse de l'IA, on réinitialise l'affichage
      if (!signalEnvoyeAuPC) {
        lcd.setCursor(0, 0);
        lcd.print("ReyCash: PRÊT   ");
        lcd.setCursor(0, 1);
        lcd.print("Déposez un objet");
      }
    }
  } 
  else {
    // Mode veille total (L'interrupteur est sur OFF) : Plus aucune lecture de distance n'est faite
    lcd.setCursor(0, 0);
    lcd.print("REYCASH DESACTIVE");
    lcd.setCursor(0, 1);
    if (etatIR == LOW) {
      lcd.print("Statut:Bac Plein");
    } else {
      lcd.print("Statut: Bac OK  ");
    }
  }

  delay(100); 
}

float mesurerDistance() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);

  long duree = pulseIn(PIN_ECHO, HIGH, 25000); 
  if (duree == 0) return -1;
  return duree * 0.034 / 2;
}

void envoyerSignalAServeur(String action) {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(serverUrl);
    http.setTimeout(2000); 
    http.addHeader("Content-Type", "application/json");
    String jsonPayload = "{\"action\":\"" + action + "\"}";
    
    int httpResponseCode = http.POST(jsonPayload);
    Serial.print("-> Code HTTP PC : ");
    Serial.println(httpResponseCode);
    
    http.end();
  }
}

// Réception des ordres de décision du PC Centre
void handlePCAction() {
  if (server.hasArg("plain")) {
    String commande = server.arg("plain");
    Serial.println("\n📥 [API RECEIVE] Ordre du PC : " + commande);
    
    // Si l'interrupteur a coupé la machine entre-temps, on ignore l'ordre du PC
    if (!systemeActive) {
      server.send(200, "application/json", "{\"status\":\"ignored_system_off\"}");
      return;
    }
    
    if (commande == "BEEP_START") {
      tone(PIN_BUZZER, 1200, 250); 
    } 
    else if (commande == "RECYCLABLE") {
      // Déchet validé !
      tone(PIN_BUZZER, 2000, 120); delay(150);
      tone(PIN_BUZZER, 2000, 120);
      
      // AJOUT LOGIQUE DU TICKET CLIENT SUR LE LCD
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("DECHET ACCEPTE !");
      lcd.setCursor(0, 1);
      lcd.print("TICKET CLIENT..."); // Écrit le statut du ticket demandé
      
      // Rotation du servo pour trier le déchet
      triServo.write(ANGLE_TRI);    
      delay(4000); // Laisse glisser l'objet pendant 4 secondes
      
      triServo.write(ANGLE_NORMAL); // Reset du servo
      delay(500);
      
      lcd.clear();
      signalEnvoyeAuPC = false; // Libère le verrou, prêt pour le déchet suivant !
    } 
    else if (commande == "INCONNU") {
      // Déchet refusé
      tone(PIN_BUZZER, 350, 800); 
      
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("DECHET INCONNU !");
      lcd.setCursor(0, 1);
      lcd.print("Pas de ticket   ");
      
      delay(3500); 
      lcd.clear();
      signalEnvoyeAuPC = false; // Libère le verrou
    }
    server.send(200, "application/json", "{\"status\":\"processed\"}");
  }
}