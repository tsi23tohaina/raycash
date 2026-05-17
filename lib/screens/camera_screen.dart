import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:camera/camera.dart';
import 'package:http/http.dart' as http;
import 'package:socket_io_client/socket_io_client.dart' as IO;

class CameraScreen extends StatefulWidget {
  final Function(Map) onResult;
  final String serverIP;
  final Function(String) onIPUpdate;

  const CameraScreen({
    super.key, 
    required this.onResult, 
    required this.serverIP, 
    required this.onIPUpdate
  });

  @override
  State<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends State<CameraScreen> {
  CameraController? _controller;
  bool _isInitialized = false;
  bool _isProcessing = false;
  IO.Socket? socket;

  @override
  void initState() {
    super.initState();
    _initCamera();
    _initSocket();
  }

  // Initialisation passive de la caméra dès le lancement de l'APK
  void _initCamera() async {
    final cameras = await availableCameras();
    if (cameras.isEmpty) return;

    _controller = CameraController(
      cameras.first, 
      ResolutionPreset.medium, 
      enableAudio: false
    );
    
    try {
      await _controller!.initialize();
      if (!mounted) return;
      setState(() => _isInitialized = true);
    } catch (e) {
      print("Erreur initialisation caméra : $e");
    }
  }

  // Connexion WebSocket pour écouter les impulsions du matériel (Hardware)
  void _initSocket() {
    socket = IO.io('http://${widget.serverIP}:5000', 
      IO.OptionBuilder()
        .setTransports(['websocket'])
        .disableAutoConnect()
        .build()
    );

    socket!.connect();

    // Reçoit le signal automatique transmis par l'ultrason
    socket!.on('command_from_esp', (data) {
      String action = data['action'];
      print("Signal matériel détecté : $action");

      // Si l'ESP32 détecte un objet à moins de 10cm, on déclenche la capture
      if (action == "START" && !_isProcessing) {
        _captureAndSend();
      }
    });

    socket!.onConnect((_) => print('Connecté au serveur WebSocket ReyCash'));
    socket!.onDisconnect((_) => print('Décommenté du serveur WebSocket'));
  }

  // Prise de photo automatique et envoi vers l'API de classification
  Future<void> _captureAndSend() async {
    if (_controller == null || !_controller!.value.isInitialized || _isProcessing) return;

    setState(() {
      _isProcessing = true;
    });

    try {
      final XFile image = await _controller!.takePicture();
      
      var request = http.MultipartRequest(
        'POST', 
        Uri.parse('http://${widget.serverIP}:5000/predict')
      );
      
      request.files.add(await http.MultipartFile.fromPath('image', image.path));
      
      var streamedResponse = await request.send();
      var response = await http.Response.fromStream(streamedResponse);

      if (response.statusCode == 200) {
        Map result = json.decode(response.body);
        // Construit l'URL complète de l'image pour l'historique de l'application
        result['full_image_url'] = 'http://${widget.serverIP}:5000${result['image_url']}';
        
        // Envoie les données vers le NavigationHub pour basculer sur l'affichage des points
        widget.onResult(result);
      } else {
        print("Erreur réponse serveur : ${response.statusCode}");
      }
    } catch (e) {
      print("Erreur lors de la capture automatique : $e");
    } finally {
      if (mounted) {
        setState(() {
          _isProcessing = false;
        });
      }
    }
  }

  // Boîte de dialogue pour modifier dynamiquement l'IP si elle change
  void _showIPDialog() {
    TextEditingController ipController = TextEditingController(text: widget.serverIP);
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text("Configuration IP Serveur"),
        content: TextField(
          controller: ipController,
          decoration: const InputDecoration(hintText: "Ex: 10.162.138.163"),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text("Annuler"),
          ),
          TextButton(
            onPressed: () {
              widget.onIPUpdate(ipController.text);
              Navigator.pop(context);
              // Réinitialise la connexion WebSocket sur la nouvelle adresse
              socket?.disconnect();
              _initSocket();
            },
            child: const Text("Enregistrer"),
          ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _controller?.dispose();
    socket?.disconnect();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_isInitialized) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator(color: Colors.teal)),
      );
    }
    
    return Scaffold(
      body: Stack(
        children: [
          // Aperçu plein écran de la caméra
          Positioned.fill(child: CameraPreview(_controller!)),
          
          // Cadre / Viseur central pour guider l'alignement du déchet
          Center(
            child: Container(
              width: 260,
              height: 260,
              decoration: BoxDecoration(
                border: Border.all(
                  color: _isProcessing ? Colors.orange : Colors.white, 
                  width: 3
                ),
                borderRadius: BorderRadius.circular(25),
              ),
            ),
          ),
          
          // Bandeau d'état inférieur de l'automate
          Positioned(
            bottom: 50, left: 20, right: 20,
            child: Center(
              child: GestureDetector(
                onLongPress: _showIPDialog, // Menu de configuration IP via appui long secret
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
                  decoration: BoxDecoration(
                    color: Colors.black87,
                    borderRadius: BorderRadius.circular(30),
                    boxShadow: const [
                      BoxShadow(color: Colors.black26, blurRadius: 10, offset: Offset(0, 4))
                    ],
                  ),
                  child: Text(
                    _isProcessing ? "🔄 CLASSIFICATION DU DÉCHET..." : "🤖 REYCASH : ATTENTE MATÉRIEL",
                    style: const TextStyle(
                      color: Colors.white, 
                      fontWeight: FontWeight.bold,
                      letterSpacing: 0.5
                    ),
                  ),
                ),
              ),
            ),
          ),
          
          // Voile de chargement fluide pendant l'inférence TFLite (Correction de Colors.black3c -> Colors.black38)
          if (_isProcessing)
            Positioned.fill(
              child: Container(
                color: Colors.black38,
                child: const Center(
                  child: CircularProgressIndicator(color: Colors.teal),
                ),
              ),
            ),
        ],
      ),
    );
  }
}