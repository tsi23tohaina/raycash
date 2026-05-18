import 'package:flutter/material.dart';
import '../services/socket_service.dart';

class StatusBadge extends StatelessWidget {
  final SocketStatus status;
  const StatusBadge({super.key, required this.status});

  @override
  Widget build(BuildContext context) {
    late Color color;
    late String label;
    late IconData icon;
    switch (status) {
      case SocketStatus.connected:
        color = Colors.green;
        label = 'EN LIGNE';
        icon = Icons.cloud_done;
        break;
      case SocketStatus.connecting:
        color = Colors.orange;
        label = 'CONNEXION...';
        icon = Icons.cloud_sync;
        break;
      case SocketStatus.disconnected:
        color = Colors.red;
        label = 'HORS LIGNE';
        icon = Icons.cloud_off;
        break;
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.black54,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: color, size: 16),
          const SizedBox(width: 6),
          Text(
            label,
            style: TextStyle(
              color: color,
              fontSize: 12,
              fontWeight: FontWeight.bold,
            ),
          ),
        ],
      ),
    );
  }
}
