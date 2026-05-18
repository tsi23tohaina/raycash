import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:raycash/models/scan_result.dart';
import 'package:raycash/models/scoring.dart';

void main() {
  group('Scoring.forLabel', () {
    test('Aluminium → 50 pts orange', () {
      final entry = Scoring.forLabel('Aluminium');
      expect(entry.points, 50);
      expect(entry.color, Colors.orange);
    });

    test('Plastique → 40 pts bleu', () {
      expect(Scoring.forLabel('Plastique').points, 40);
    });

    test('Verre → 20 pts vert', () {
      expect(Scoring.forLabel('Verre').points, 20);
    });

    test('Papier et Carton → 10 pts marron', () {
      expect(Scoring.forLabel('Papier').points, 10);
      expect(Scoring.forLabel('Carton').points, 10);
      expect(Scoring.forLabel('Papier').color, Colors.brown);
    });

    test('Label inconnu → 0 pts gris', () {
      final entry = Scoring.forLabel('Banane');
      expect(entry.points, 0);
      expect(entry.color, Colors.grey);
    });

    test('Strip les chiffres du label (ex: "Plastique3")', () {
      expect(Scoring.forLabel('Plastique3').points, 40);
      expect(Scoring.forLabel('Aluminium 1').points, 50);
    });

    test('Label vide → 0 pts', () {
      expect(Scoring.forLabel('').points, 0);
    });
  });

  group('Scoring.pointsOf', () {
    test('Priorité au champ serveur', () {
      const scan = ScanResult(
        label: 'Aluminium',
        confidence: '99%',
        imageUrl: '',
        fullImageUrl: '',
        triStatus: 'RECYCLABLE',
        points: 999,
      );
      expect(Scoring.pointsOf(scan), 999);
    });

    test('Fallback sur barème client si serveur ne renvoie pas points', () {
      const scan = ScanResult(
        label: 'Aluminium',
        confidence: '99%',
        imageUrl: '',
        fullImageUrl: '',
        triStatus: 'RECYCLABLE',
      );
      expect(Scoring.pointsOf(scan), 50);
    });
  });
}
