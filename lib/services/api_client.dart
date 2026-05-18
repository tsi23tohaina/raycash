import 'dart:async';
import 'dart:convert';
import 'dart:developer' as developer;
import 'package:camera/camera.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import '../models/scan_result.dart';

void _log(String message, {Object? error, StackTrace? stackTrace}) {
  developer.log(message, name: 'raycash.api', error: error, stackTrace: stackTrace);
}

sealed class ApiResult<T> {
  const ApiResult();
}

class ApiSuccess<T> extends ApiResult<T> {
  final T data;
  const ApiSuccess(this.data);
}

class ApiFailure<T> extends ApiResult<T> {
  final ApiError error;
  final String? message;
  const ApiFailure(this.error, {this.message});
}

enum ApiError {
  unauthorized,
  rateLimited,
  timeout,
  unreachable,
  serverError,
  badResponse,
  unknown,
}

class ApiClient {
  final String apiKey;
  final Duration timeout;

  const ApiClient({
    required this.apiKey,
    this.timeout = const Duration(seconds: 20),
  });

  String baseUrl(String serverIP) => 'http://$serverIP:5000';

  Map<String, String> get _authHeaders => {'X-API-Key': apiKey};

  Map<String, String> imageHeaders() => Map.unmodifiable(_authHeaders);

  /// Mono-vue (compat rétro). Délègue à `classifyImages([image])`.
  Future<ApiResult<ScanResult>> classifyImage({
    required String serverIP,
    required XFile image,
  }) =>
      classifyImages(serverIP: serverIP, images: [image]);

  /// Multi-vue : envoie 1 à N images au serveur via `image[]`.
  /// Le serveur (V4) moyenne les prédictions sur toutes les vues + tous les
  /// modèles ensemble pour une décision plus fiable.
  Future<ApiResult<ScanResult>> classifyImages({
    required String serverIP,
    required List<XFile> images,
  }) async {
    if (images.isEmpty) {
      return const ApiFailure(ApiError.badResponse, message: 'no images provided');
    }
    final base = baseUrl(serverIP);
    final uri = Uri.parse('$base/predict');

    try {
      final request = http.MultipartRequest('POST', uri);
      request.headers.addAll(_authHeaders);

      // `XFile.readAsBytes()` marche aussi bien sur web (blob URL) que sur
      // mobile (vrai fichier).
      for (var i = 0; i < images.length; i++) {
        final img = images[i];
        final bytes = await img.readAsBytes();
        final filename = img.name.isNotEmpty ? img.name : 'capture_$i.jpg';
        request.files.add(
          http.MultipartFile.fromBytes(
            'image[]', // multi-vue : on utilise toujours image[] côté serveur
            bytes,
            filename: filename,
            contentType: MediaType('image', 'jpeg'),
          ),
        );
      }

      final streamed = await request.send().timeout(timeout);
      final response = await http.Response.fromStream(streamed);

      switch (response.statusCode) {
        case 200:
          final decoded = json.decode(response.body);
          if (decoded is! Map) {
            return const ApiFailure(ApiError.badResponse);
          }
          final scan = ScanResult.fromServer(
            Map<String, dynamic>.from(decoded),
            base,
          );
          return ApiSuccess(scan);
        case 401:
          return const ApiFailure(ApiError.unauthorized);
        case 429:
          return const ApiFailure(ApiError.rateLimited);
        default:
          _log('Réponse serveur ${response.statusCode}: ${response.body}');
          return ApiFailure(
            ApiError.serverError,
            message: 'HTTP ${response.statusCode}',
          );
      }
    } on TimeoutException {
      return const ApiFailure(ApiError.timeout);
    } on http.ClientException catch (e, st) {
      _log('ClientException /predict', error: e, stackTrace: st);
      return const ApiFailure(ApiError.unreachable);
    } catch (e, st) {
      _log('Erreur inattendue /predict', error: e, stackTrace: st);
      return ApiFailure(ApiError.unknown, message: e.toString());
    }
  }
}
