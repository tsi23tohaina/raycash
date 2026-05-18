import 'package:flutter_test/flutter_test.dart';
import 'package:raycash/models/scan_result.dart';

void main() {
  group('ScanResult.fromServer', () {
    test('mappe correctement les champs serveur', () {
      final scan = ScanResult.fromServer(
        const {
          'label': 'Plastique',
          'confidence': '87.3%',
          'image_url': '/uploads/capture_42.jpg',
          'tri_status': 'RECYCLABLE',
          'points': 40,
        },
        'http://10.0.0.5:5000',
      );

      expect(scan.label, 'Plastique');
      expect(scan.confidence, '87.3%');
      expect(scan.imageUrl, '/uploads/capture_42.jpg');
      expect(scan.fullImageUrl, 'http://10.0.0.5:5000/uploads/capture_42.jpg');
      expect(scan.triStatus, 'RECYCLABLE');
      expect(scan.points, 40);
    });

    test('valeurs par défaut si champs manquants', () {
      final scan = ScanResult.fromServer(const {}, 'http://localhost:5000');
      expect(scan.label, 'Inconnu');
      expect(scan.confidence, '');
      expect(scan.imageUrl, '');
      expect(scan.fullImageUrl, '');
      expect(scan.triStatus, 'NON_RECYCLABLE');
      expect(scan.points, isNull);
    });

    test('image_url vide → fullImageUrl vide (pas de concat)', () {
      final scan = ScanResult.fromServer(
        const {'image_url': ''},
        'http://localhost:5000',
      );
      expect(scan.fullImageUrl, '');
    });
  });

  group('ScanResult round-trip (toJson/fromJson)', () {
    test('préserve tous les champs', () {
      const original = ScanResult(
        label: 'Aluminium',
        confidence: '92.0%',
        imageUrl: '/uploads/x.jpg',
        fullImageUrl: 'http://h/uploads/x.jpg',
        triStatus: 'RECYCLABLE',
        points: 50,
      );

      final restored = ScanResult.fromJson(original.toJson());
      expect(restored.label, original.label);
      expect(restored.confidence, original.confidence);
      expect(restored.imageUrl, original.imageUrl);
      expect(restored.fullImageUrl, original.fullImageUrl);
      expect(restored.triStatus, original.triStatus);
      expect(restored.points, original.points);
    });

    test('points absent dans le JSON → null', () {
      final restored = ScanResult.fromJson(const {
        'label': 'Verre',
        'confidence': '50%',
        'image_url': '',
        'full_image_url': '',
        'tri_status': 'RECYCLABLE',
      });
      expect(restored.points, isNull);
    });
  });
}
