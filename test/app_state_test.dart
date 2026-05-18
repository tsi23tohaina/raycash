import 'package:flutter_test/flutter_test.dart';
import 'package:raycash/models/scan_result.dart';
import 'package:raycash/services/socket_service.dart';
import 'package:raycash/services/storage_service.dart';
import 'package:raycash/state/app_state.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _FakeSocketService extends SocketService {
  String? connectedTo;
  int connectCalls = 0;

  _FakeSocketService() : super(apiKey: 'test');

  @override
  void connect({required String serverIP, required SignalHandler onSignal}) {
    connectedTo = serverIP;
    connectCalls++;
  }

  @override
  void reconnectWith({required String serverIP, required SignalHandler onSignal}) {
    connectedTo = serverIP;
    connectCalls++;
  }

  @override
  Future<void> dispose() async {}
}

ScanResult _scan({required String label, int? points}) => ScanResult(
      label: label,
      confidence: '90%',
      imageUrl: '',
      fullImageUrl: '',
      triStatus: 'RECYCLABLE',
      points: points,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues(const {});
  });

  test('bootstrap charge l\'IP et les scans depuis le storage', () async {
    SharedPreferences.setMockInitialValues(const {
      'raycash.serverIP': '10.0.0.42',
      'raycash.scans':
          '[{"label":"Plastique","confidence":"90%","image_url":"","full_image_url":"","tri_status":"RECYCLABLE","points":40}]',
    });
    final socket = _FakeSocketService();
    final state = AppState(storage: StorageService(), socket: socket);

    await state.bootstrap(onSignal: (_) {});

    expect(state.serverIP, '10.0.0.42');
    expect(state.scans, hasLength(1));
    expect(state.scans.first.label, 'Plastique');
    expect(state.totalPoints, 40);
    expect(socket.connectedTo, '10.0.0.42');
    expect(state.ready, isTrue);
  });

  test('addScan ajoute et met à jour totalPoints', () async {
    final state = AppState(storage: StorageService(), socket: _FakeSocketService());
    await state.bootstrap(onSignal: (_) {});

    await state.addScan(_scan(label: 'Aluminium', points: 50));
    await state.addScan(_scan(label: 'Plastique', points: 40));

    expect(state.scans, hasLength(2));
    expect(state.totalPoints, 90);
  });

  test('resetSession vide les scans', () async {
    final state = AppState(storage: StorageService(), socket: _FakeSocketService());
    await state.bootstrap(onSignal: (_) {});
    await state.addScan(_scan(label: 'Verre', points: 20));

    await state.resetSession();

    expect(state.scans, isEmpty);
    expect(state.totalPoints, 0);
  });

  test('updateServerIP persiste et reconnecte le socket', () async {
    final socket = _FakeSocketService();
    final state = AppState(storage: StorageService(), socket: socket);
    await state.bootstrap(onSignal: (_) {});

    final initialCalls = socket.connectCalls;
    await state.updateServerIP('192.168.1.50');

    expect(state.serverIP, '192.168.1.50');
    expect(socket.connectCalls, greaterThan(initialCalls));
    expect(socket.connectedTo, '192.168.1.50');
  });

  test('updateServerIP no-op si la même IP', () async {
    final socket = _FakeSocketService();
    final state = AppState(storage: StorageService(), socket: socket);
    await state.bootstrap(onSignal: (_) {});

    final callsBefore = socket.connectCalls;
    await state.updateServerIP(state.serverIP);

    expect(socket.connectCalls, callsBefore);
  });

  test('totalPoints ignore les scans sans points', () async {
    final state = AppState(storage: StorageService(), socket: _FakeSocketService());
    await state.bootstrap(onSignal: (_) {});
    await state.addScan(_scan(label: 'Aluminium', points: 50));
    await state.addScan(_scan(label: 'Banane')); // pas de points
    expect(state.totalPoints, 50);
  });
}
