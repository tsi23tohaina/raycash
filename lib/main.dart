import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'services/socket_service.dart';
import 'services/storage_service.dart';
import 'state/app_state.dart';
import 'screens/camera_screen.dart';
import 'screens/data_screen.dart';

const String _apiKey =
    String.fromEnvironment('RAYCASH_API_KEY', defaultValue: '');

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ReycashApp());
}

class ReycashApp extends StatelessWidget {
  const ReycashApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider<AppState>(
      create: (_) => AppState(
        storage: StorageService(),
        socket: SocketService(apiKey: _apiKey),
      ),
      child: MaterialApp(
        debugShowCheckedModeBanner: false,
        title: 'ReyCash',
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal),
          useMaterial3: true,
        ),
        home: const NavigationHub(),
      ),
    );
  }
}

class NavigationHub extends StatefulWidget {
  const NavigationHub({super.key});

  @override
  State<NavigationHub> createState() => _NavigationHubState();
}

class _NavigationHubState extends State<NavigationHub> {
  int _currentIndex = 0;
  final GlobalKey<CameraScreenState> _cameraKey = GlobalKey<CameraScreenState>();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<AppState>().bootstrap(onSignal: _onHardwareSignal);
    });
  }

  void _onHardwareSignal(String action) {
    if (action == 'START') {
      _cameraKey.currentState?.triggerCapture();
    }
  }

  @override
  Widget build(BuildContext context) {
    final pages = <Widget>[
      CameraScreen(key: _cameraKey),
      const DataScreen(),
    ];
    return Scaffold(
      body: pages[_currentIndex],
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _currentIndex,
        onTap: (i) => setState(() => _currentIndex = i),
        selectedItemColor: Colors.teal,
        items: const [
          BottomNavigationBarItem(icon: Icon(Icons.camera_alt), label: 'Scanner'),
          BottomNavigationBarItem(icon: Icon(Icons.receipt_long), label: 'Session'),
        ],
      ),
    );
  }
}
