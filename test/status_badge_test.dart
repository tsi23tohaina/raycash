import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:raycash/services/socket_service.dart';
import 'package:raycash/widgets/status_badge.dart';

Future<void> _pump(WidgetTester tester, SocketStatus status) async {
  await tester.pumpWidget(
    MaterialApp(home: Scaffold(body: StatusBadge(status: status))),
  );
}

void main() {
  testWidgets('Affiche "EN LIGNE" en vert quand connected', (tester) async {
    await _pump(tester, SocketStatus.connected);
    expect(find.text('EN LIGNE'), findsOneWidget);
    expect(find.byIcon(Icons.cloud_done), findsOneWidget);
  });

  testWidgets('Affiche "CONNEXION..." quand connecting', (tester) async {
    await _pump(tester, SocketStatus.connecting);
    expect(find.text('CONNEXION...'), findsOneWidget);
    expect(find.byIcon(Icons.cloud_sync), findsOneWidget);
  });

  testWidgets('Affiche "HORS LIGNE" en rouge quand disconnected', (tester) async {
    await _pump(tester, SocketStatus.disconnected);
    expect(find.text('HORS LIGNE'), findsOneWidget);
    expect(find.byIcon(Icons.cloud_off), findsOneWidget);
  });
}
