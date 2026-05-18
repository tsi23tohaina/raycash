import 'dart:async';
import 'package:flutter/foundation.dart';
import '../models/scan_result.dart';
import '../services/socket_service.dart';
import '../services/storage_service.dart';

/// Source unique de vérité pour l'état partagé de l'app.
///
/// Encapsule : IP du serveur, statut WebSocket, liste des scans persistés.
/// Les écrans consomment ces données via Provider et ne se passent plus
/// d'informations entre eux via des callbacks.
class AppState extends ChangeNotifier {
  final StorageService _storage;
  final SocketService _socket;

  String _serverIP = '127.0.0.1';
  SocketStatus _socketStatus = SocketStatus.disconnected;
  List<ScanResult> _scans = [];
  bool _ready = false;

  StreamSubscription<SocketStatus>? _statusSub;
  void Function(String action)? _signalHandler;

  AppState({
    required StorageService storage,
    required SocketService socket,
  })  : _storage = storage,
        _socket = socket {
    _statusSub = _socket.status.listen((s) {
      _socketStatus = s;
      notifyListeners();
    });
  }

  String get serverIP => _serverIP;
  SocketStatus get socketStatus => _socketStatus;
  List<ScanResult> get scans => List.unmodifiable(_scans);
  bool get ready => _ready;

  int get totalPoints {
    int sum = 0;
    for (final s in _scans) {
      sum += s.points ?? 0;
    }
    return sum;
  }

  Future<void> bootstrap({required void Function(String) onSignal}) async {
    final loadedIP = await _storage.loadServerIP();
    if (loadedIP != null && loadedIP.isNotEmpty) {
      _serverIP = loadedIP;
    }
    _scans = await _storage.loadScans();
    _signalHandler = onSignal;
    _socket.connect(serverIP: _serverIP, onSignal: onSignal);
    _ready = true;
    notifyListeners();
  }

  Future<void> updateServerIP(String newIP) async {
    if (newIP == _serverIP) return;
    _serverIP = newIP;
    await _storage.saveServerIP(newIP);
    final handler = _signalHandler;
    if (handler != null) {
      _socket.reconnectWith(serverIP: newIP, onSignal: handler);
    }
    notifyListeners();
  }

  Future<void> addScan(ScanResult scan) async {
    _scans = [..._scans, scan];
    await _storage.saveScans(_scans);
    notifyListeners();
  }

  Future<void> resetSession() async {
    _scans = [];
    await _storage.saveScans(_scans);
    notifyListeners();
  }

  @override
  Future<void> dispose() async {
    await _statusSub?.cancel();
    await _socket.dispose();
    super.dispose();
  }
}
