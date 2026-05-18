import 'dart:async';
import 'dart:developer' as developer;
import 'package:socket_io_client/socket_io_client.dart' as IO;

void _log(String message, {Object? error, StackTrace? stackTrace}) {
  developer.log(message, name: 'raycash.socket', error: error, stackTrace: stackTrace);
}

enum SocketStatus { connecting, connected, disconnected }

typedef SignalHandler = void Function(String action);

class SocketService {
  final String apiKey;
  IO.Socket? _socket;
  String? _currentIP;

  final _statusController = StreamController<SocketStatus>.broadcast();
  Stream<SocketStatus> get status => _statusController.stream;
  SocketStatus _lastStatus = SocketStatus.disconnected;
  SocketStatus get lastStatus => _lastStatus;

  SocketService({required this.apiKey});

  void _emitStatus(SocketStatus s) {
    _lastStatus = s;
    if (!_statusController.isClosed) _statusController.add(s);
  }

  void connect({
    required String serverIP,
    required SignalHandler onSignal,
  }) {
    _currentIP = serverIP;
    _emitStatus(SocketStatus.connecting);

    _socket?.disconnect();
    _socket?.dispose();

    final socket = IO.io(
      'http://$serverIP:5000',
      IO.OptionBuilder()
          .setTransports(['websocket'])
          .setExtraHeaders({'X-API-Key': apiKey})
          .enableReconnection()
          .setReconnectionAttempts(double.maxFinite.toInt())
          .setReconnectionDelay(2000)
          .setReconnectionDelayMax(10000)
          .setTimeout(10000)
          .disableAutoConnect()
          .build(),
    );

    socket.connect();

    socket.on('command_from_esp', (data) {
      final action = (data is Map ? data['action'] : null) as String? ?? '';
      _log('Signal matériel : $action');
      if (action.isNotEmpty) onSignal(action);
    });

    socket.onConnect((_) {
      _log('WebSocket connecté');
      _emitStatus(SocketStatus.connected);
    });

    socket.onDisconnect((_) {
      _log('WebSocket déconnecté');
      _emitStatus(SocketStatus.disconnected);
    });

    socket.onConnectError((err) {
      _log('Erreur connexion WebSocket', error: err);
      _emitStatus(SocketStatus.disconnected);
    });

    socket.onReconnectAttempt((attempt) {
      _log('Reconnexion WebSocket tentative #$attempt');
      _emitStatus(SocketStatus.connecting);
    });

    _socket = socket;
  }

  void reconnectWith({
    required String serverIP,
    required SignalHandler onSignal,
  }) {
    if (_currentIP == serverIP && _socket?.connected == true) return;
    connect(serverIP: serverIP, onSignal: onSignal);
  }

  Future<void> dispose() async {
    _socket?.disconnect();
    _socket?.dispose();
    _socket = null;
    await _statusController.close();
  }
}
