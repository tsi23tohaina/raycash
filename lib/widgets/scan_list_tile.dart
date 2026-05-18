import 'package:flutter/material.dart';
import '../models/scan_result.dart';
import '../models/scoring.dart';

class ScanListTile extends StatelessWidget {
  final ScanResult scan;
  final Map<String, String> imageHeaders;
  const ScanListTile({
    super.key,
    required this.scan,
    required this.imageHeaders,
  });

  @override
  Widget build(BuildContext context) {
    final points = Scoring.pointsOf(scan);
    final color = Scoring.colorOf(scan);
    return ListTile(
      leading: scan.fullImageUrl.isEmpty
          ? const Icon(Icons.image_not_supported, size: 50)
          : Image.network(
              scan.fullImageUrl,
              width: 50,
              height: 50,
              fit: BoxFit.cover,
              headers: imageHeaders,
              errorBuilder: (_, __, ___) =>
                  const Icon(Icons.broken_image, size: 50),
            ),
      title: Text(scan.label.toUpperCase()),
      subtitle: Text('Confiance : ${scan.confidence}'),
      trailing: Text(
        '+$points pts',
        style: TextStyle(color: color, fontWeight: FontWeight.bold),
      ),
    );
  }
}
