import 'dart:convert';
import 'dart:developer' as developer;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/scan_result.dart';

void _log(String message, {Object? error, StackTrace? stackTrace}) {
  developer.log(message, name: 'raycash.storage', error: error, stackTrace: stackTrace);
}

class StorageService {
  static const String _scansKey = 'raycash.scans';
  static const String _ipKey = 'raycash.serverIP';

  Future<SharedPreferences> get _prefs => SharedPreferences.getInstance();

  Future<List<ScanResult>> loadScans() async {
    try {
      final prefs = await _prefs;
      final raw = prefs.getString(_scansKey);
      if (raw == null || raw.isEmpty) return [];
      final decoded = json.decode(raw);
      if (decoded is! List) return [];
      return decoded
          .whereType<Map>()
          .map((e) => ScanResult.fromJson(Map<String, dynamic>.from(e)))
          .toList();
    } catch (e, st) {
      _log('loadScans a échoué', error: e, stackTrace: st);
      return [];
    }
  }

  Future<void> saveScans(List<ScanResult> scans) async {
    try {
      final prefs = await _prefs;
      final raw = json.encode(scans.map((s) => s.toJson()).toList());
      await prefs.setString(_scansKey, raw);
    } catch (e, st) {
      _log('saveScans a échoué', error: e, stackTrace: st);
    }
  }

  Future<String?> loadServerIP() async {
    try {
      final prefs = await _prefs;
      return prefs.getString(_ipKey);
    } catch (e, st) {
      _log('loadServerIP a échoué', error: e, stackTrace: st);
      return null;
    }
  }

  Future<void> saveServerIP(String ip) async {
    try {
      final prefs = await _prefs;
      await prefs.setString(_ipKey, ip);
    } catch (e, st) {
      _log('saveServerIP a échoué', error: e, stackTrace: st);
    }
  }
}
