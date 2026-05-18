#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <ESP32Servo.h>
#include "secrets.h"

// --- CONFIGURATION WI-FI (cf. secrets.h) ---
const char* ssid = WIFI_SSID;
const char* password = WIFI_PASSWORD;

// --- CONFIGURATION SERVEUR (cf. secrets.h) ---
const char* serverUrl = SERVER_URL;
const char* apiKey = RAYCASH_API_KEY;

// Définition des broches
const int pinBouton = 4;
const int pinBuzzer = 18;
const int pinServo  = 19;

// Variables d'état
int compteur = 0;
bool dernierEtatBouton = HIGH;
unsigned long dernierTempsDebounce = 0;
unsigned long delaiDebounce = 50;

// Serveur HTTP local (port 80) pour recevoir les ordres /servo du serveur Flask.
WebServer http(80);
Servo trappe;

// Position de repos / RECYCLABLE / INCONNU
const int SERVO_REPOS      = 90;
const int SERVO_RECYCLABLE = 180;
const int SERVO_INCONNU    = 0;
const unsigned long PIVOT_DELAY_MS = 1500;

void setup() {
  Serial.begin(115200);

  pinMode(pinBouton, INPUT_PULLUP);
  pinMode(pinBuzzer, OUTPUT);

  trappe.attach(pinServo);
  trappe.write(SERVO_REPOS);

  // Connexion au Wi-Fi
  WiFi.begin(ssid, password);
  Serial.print("Connexion au Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWi-Fi connecté !");
  Serial.print("IP ESP32 : ");
  Serial.println(WiFi.localIP());
  Serial.println("Système RecyCash prêt.");

  // Routes du serveur HTTP local
  http.on("/servo", HTTP_POST, handleServo);
  http.on("/health", HTTP_GET, []() {
    http.send(200, "application/json", "{\"status\":\"ok\"}");
  });
  http.onNotFound([]() {
    http.send(404, "application/json", "{\"error\":\"not_found\"}");
  });
  http.begin();
}

void loop() {
  http.handleClient();

  int lecture = digitalRead(pinBouton);

  if (lecture == LOW && dernierEtatBouton == HIGH && (millis() - dernierTempsDebounce) > delaiDebounce) {
    compteur++;
    dernierTempsDebounce = millis();

    if (compteur == 1) {
      Serial.println("Action: Démarrer");
      jouerSon(1000, 500);
      envoyerSignalAServeur("START");
    }
    else if (compteur == 2) {
      Serial.println("Action: Arrêter");
      jouerSon(500, 500);
      envoyerSignalAServeur("STOP");
      compteur = 0;
    }
  }
  dernierEtatBouton = lecture;
}

// Vérifie le header X-API-Key. Renvoie true si valide, sinon répond 401 directement.
bool authentifierRequete() {
  if (!http.hasHeader("X-API-Key") || http.header("X-API-Key") != String(apiKey)) {
    http.send(401, "application/json", "{\"error\":\"unauthorized\"}");
    return false;
  }
  return true;
}

// Reçoit l'ordre du serveur Flask après classification : RECYCLABLE / INCONNU.
void handleServo() {
  http.collectHeaders((const char*[]){"X-API-Key"}, 1);
  if (!authentifierRequete()) return;

  String body = http.arg("plain");
  body.trim();
  Serial.print("Ordre servo reçu : ");
  Serial.println(body);

  if (body == "RECYCLABLE") {
    trappe.write(SERVO_RECYCLABLE);
  } else if (body == "INCONNU") {
    trappe.write(SERVO_INCONNU);
  } else {
    http.send(400, "application/json", "{\"error\":\"action_invalide\"}");
    return;
  }

  delay(PIVOT_DELAY_MS);
  trappe.write(SERVO_REPOS);
  http.send(200, "application/json", "{\"status\":\"done\"}");
}

// Fonction pour communiquer avec Flask
void envoyerSignalAServeur(String action) {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient client;

    client.begin(serverUrl);
    client.addHeader("Content-Type", "application/json");
    client.addHeader("X-API-Key", apiKey);

    String jsonPayload = "{\"action\":\"" + action + "\"}";

    int httpResponseCode = client.POST(jsonPayload);

    if (httpResponseCode > 0) {
      Serial.print("Réponse serveur : ");
      Serial.println(httpResponseCode);
    } else {
      Serial.print("Erreur d'envoi : ");
      Serial.println(httpResponseCode);
    }

    client.end();
  } else {
    Serial.println("Erreur : Wi-Fi déconnecté");
  }
}

void jouerSon(int frequence, int duree) {
  tone(pinBuzzer, frequence);
  delay(duree);
  noTone(pinBuzzer);
}
