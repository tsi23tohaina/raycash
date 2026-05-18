import 'package:flutter/material.dart';
import 'scan_result.dart';

class ScoringEntry {
  final int points;
  final Color color;
  const ScoringEntry(this.points, this.color);
}

/// Barème de fallback côté client.
///
/// La source de vérité est le serveur : `ScanResult.points` est renvoyé par
/// `/predict`. Ce barème ne sert qu'à afficher quelque chose de cohérent si
/// le serveur ne renvoie pas le champ (ancienne version, mode dégradé).
class Scoring {
  static const Map<String, ScoringEntry> _table = {
    'Aluminium': ScoringEntry(50, Colors.orange),
    'Plastique': ScoringEntry(40, Colors.blue),
    'Verre': ScoringEntry(20, Colors.green),
    'Papier': ScoringEntry(10, Colors.brown),
    'Carton': ScoringEntry(10, Colors.brown),
  };

  static const ScoringEntry _unknown = ScoringEntry(0, Colors.grey);

  static ScoringEntry forLabel(String rawLabel) {
    final clean = rawLabel.replaceAll(RegExp(r'[0-9]'), '').trim();
    return _table[clean] ?? _unknown;
  }

  /// Points effectifs pour un scan : priorité au champ serveur.
  static int pointsOf(ScanResult scan) =>
      scan.points ?? forLabel(scan.label).points;

  static Color colorOf(ScanResult scan) => forLabel(scan.label).color;
}
