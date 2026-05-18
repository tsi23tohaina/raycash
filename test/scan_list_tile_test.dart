import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:raycash/models/scan_result.dart';
import 'package:raycash/widgets/scan_list_tile.dart';

void main() {
  testWidgets('Affiche label en majuscules, confiance et points', (tester) async {
    const scan = ScanResult(
      label: 'Plastique',
      confidence: '87.3%',
      imageUrl: '',
      fullImageUrl: '',
      triStatus: 'RECYCLABLE',
      points: 40,
    );
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: ScanListTile(scan: scan, imageHeaders: {}),
      ),
    ));
    expect(find.text('PLASTIQUE'), findsOneWidget);
    expect(find.text('Confiance : 87.3%'), findsOneWidget);
    expect(find.text('+40 pts'), findsOneWidget);
  });

  testWidgets('Sans points serveur, fallback sur le barème client', (tester) async {
    const scan = ScanResult(
      label: 'Aluminium',
      confidence: '90.0%',
      imageUrl: '',
      fullImageUrl: '',
      triStatus: 'RECYCLABLE',
    );
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: ScanListTile(scan: scan, imageHeaders: {}),
      ),
    ));
    expect(find.text('+50 pts'), findsOneWidget);
  });

  testWidgets('fullImageUrl vide → icône image_not_supported', (tester) async {
    const scan = ScanResult(
      label: 'Verre',
      confidence: '70%',
      imageUrl: '',
      fullImageUrl: '',
      triStatus: 'RECYCLABLE',
      points: 20,
    );
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: ScanListTile(scan: scan, imageHeaders: {}),
      ),
    ));
    expect(find.byIcon(Icons.image_not_supported), findsOneWidget);
  });
}
