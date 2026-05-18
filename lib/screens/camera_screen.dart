import 'dart:async';
import 'dart:developer' as developer;
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_client.dart';
import '../services/socket_service.dart';
import '../state/app_state.dart';
import '../widgets/status_badge.dart';

void _log(String message, {Object? error, StackTrace? stackTrace}) {
  developer.log(message, name: 'raycash.camera', error: error, stackTrace: stackTrace);
}

const String _apiKey =
    String.fromEnvironment('RAYCASH_API_KEY', defaultValue: '');

class CameraScreen extends StatefulWidget {
  const CameraScreen({super.key});

  @override
  State<CameraScreen> createState() => CameraScreenState();
}

class CameraScreenState extends State<CameraScreen> {
  final ApiClient _api = const ApiClient(apiKey: _apiKey);

  CameraController? _controller;
  bool _isInitialized = false;
  bool _isProcessing = false;

  @override
  void initState() {
    super.initState();
    _initCamera();
  }

  void _showSnack(String message, {bool isError = false, bool isUncertain = false}) {
    if (!mounted) return;
    Color? bg;
    if (isUncertain) {
      bg = Colors.orange.shade800;  // signal "incertain" distinct des erreurs réseau
    } else if (isError) {
      bg = Colors.red.shade700;
    }
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: bg,
        duration: Duration(seconds: isUncertain ? 4 : 3),
      ),
    );
  }

  Future<void> _initCamera() async {
    try {
      final cameras = await availableCameras();
      if (cameras.isEmpty) {
        _showSnack('Aucune caméra disponible', isError: true);
        return;
      }
      _controller = CameraController(
        cameras.first,
        ResolutionPreset.medium,
        enableAudio: false,
      );
      await _controller!.initialize();
      if (!mounted) return;
      setState(() => _isInitialized = true);
    } catch (e, st) {
      _log("Erreur init caméra", error: e, stackTrace: st);
      _showSnack("Impossible d'initialiser la caméra", isError: true);
    }
  }

  /// Nombre de photos par scan. 3 = compromis fiabilité/latence.
  static const int _nbCaptures = 3;
  static const Duration _delayBetweenCaptures = Duration(milliseconds: 400);

  /// Déclenche capture multi-vue + envoi serveur. Exposé pour être appelé par
  /// le `NavigationHub` quand l'ESP32 signale "START".
  ///
  /// Prend [_nbCaptures] photos successives avec un petit délai entre chaque
  /// (l'objet peut bouger un peu, la lumière varie légèrement). Le serveur
  /// fait ensuite un vote ensemble sur toutes les vues × tous les modèles.
  Future<void> triggerCapture() async {
    if (_controller == null ||
        !_controller!.value.isInitialized ||
        _isProcessing) {
      return;
    }
    setState(() => _isProcessing = true);
    final appState = context.read<AppState>();

    try {
      final List<XFile> images = [];
      for (var i = 0; i < _nbCaptures; i++) {
        if (i > 0) await Future.delayed(_delayBetweenCaptures);
        if (!mounted) return;
        final shot = await _controller!.takePicture();
        images.add(shot);
      }

      final result = await _api.classifyImages(
        serverIP: appState.serverIP,
        images: images,
      );

      switch (result) {
        case ApiSuccess(:final data):
          if (data.accepted) {
            await appState.addScan(data);
            _showSnack('+${data.label} (${data.confidence})');
          } else {
            // Rejet par les seuils d'incertitude : on n'enregistre PAS dans
            // la session (pas de faux positifs) et on demande à rescanner.
            _showSnack(
              data.hint ?? 'Pas sûr — repositionne et réessaie',
              isUncertain: true,
            );
          }
        case ApiFailure(:final error):
          _showSnack(_messageFor(error), isError: true);
      }
    } catch (e, st) {
      _log('Capture en erreur', error: e, stackTrace: st);
      _showSnack('Erreur lors de la capture.', isError: true);
    } finally {
      if (mounted) setState(() => _isProcessing = false);
    }
  }

  String _messageFor(ApiError e) {
    switch (e) {
      case ApiError.unauthorized:
        return 'Clé API rejetée. Vérifie RAYCASH_API_KEY.';
      case ApiError.rateLimited:
        return 'Trop de requêtes. Patiente quelques secondes.';
      case ApiError.timeout:
        return 'Le serveur ne répond pas. Réessaye.';
      case ApiError.unreachable:
        return "Serveur injoignable. Vérifie l'IP et le réseau.";
      case ApiError.serverError:
        return 'Erreur serveur.';
      case ApiError.badResponse:
        return 'Réponse serveur inattendue.';
      case ApiError.unknown:
        return 'Erreur inconnue.';
    }
  }

  void _showIPDialog() {
    final appState = context.read<AppState>();
    final controller = TextEditingController(text: appState.serverIP);
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Configuration IP Serveur'),
        content: TextField(
          controller: controller,
          decoration: const InputDecoration(hintText: 'Ex: 10.162.138.163'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Annuler'),
          ),
          TextButton(
            onPressed: () {
              appState.updateServerIP(controller.text.trim());
              Navigator.pop(ctx);
            },
            child: const Text('Enregistrer'),
          ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_isInitialized) {
      return const Scaffold(
        body: Center(child: CircularProgressIndicator(color: Colors.teal)),
      );
    }

    final socketStatus = context.select<AppState, SocketStatus>((s) => s.socketStatus);

    return Scaffold(
      body: Stack(
        children: [
          Positioned.fill(child: CameraPreview(_controller!)),
          Center(
            child: Container(
              width: 260,
              height: 260,
              decoration: BoxDecoration(
                border: Border.all(
                  color: _isProcessing ? Colors.orange : Colors.white,
                  width: 3,
                ),
                borderRadius: BorderRadius.circular(25),
              ),
            ),
          ),
          Positioned(
            top: 40,
            right: 16,
            child: StatusBadge(status: socketStatus),
          ),
          Positioned(
            bottom: 50,
            left: 20,
            right: 20,
            child: Center(
              child: GestureDetector(
                onLongPress: _showIPDialog,
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
                  decoration: BoxDecoration(
                    color: Colors.black87,
                    borderRadius: BorderRadius.circular(30),
                    boxShadow: const [
                      BoxShadow(
                          color: Colors.black26,
                          blurRadius: 10,
                          offset: Offset(0, 4))
                    ],
                  ),
                  child: Text(
                    _isProcessing
                        ? '🔄 CLASSIFICATION DU DÉCHET...'
                        : '🤖 REYCASH : ATTENTE MATÉRIEL',
                    style: const TextStyle(
                      color: Colors.white,
                      fontWeight: FontWeight.bold,
                      letterSpacing: 0.5,
                    ),
                  ),
                ),
              ),
            ),
          ),
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
