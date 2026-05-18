import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_client.dart';
import '../state/app_state.dart';
import '../widgets/scan_list_tile.dart';

const String _apiKey =
    String.fromEnvironment('RAYCASH_API_KEY', defaultValue: '');

class DataScreen extends StatelessWidget {
  const DataScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final scans = state.scans;
    final totalPoints = state.totalPoints;
    final imageHeaders = const ApiClient(apiKey: _apiKey).imageHeaders();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Ma Récolte ReyCash'),
        actions: [
          IconButton(
            onPressed: () => context.read<AppState>().resetSession(),
            icon: const Icon(Icons.delete_sweep, color: Colors.red),
          ),
        ],
      ),
      body: scans.isEmpty
          ? const Center(child: Text('Aucun déchet scanné. Commencez le scan !'))
          : Column(
              children: [
                Container(
                  padding: const EdgeInsets.all(20),
                  color: Colors.teal.shade50,
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('Total : ${scans.length} objets',
                          style: const TextStyle(fontSize: 18)),
                      Text(
                        '$totalPoints Points',
                        style: const TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.bold,
                          color: Colors.teal,
                        ),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: ListView.builder(
                    itemCount: scans.length,
                    itemBuilder: (context, index) => ScanListTile(
                      scan: scans[index],
                      imageHeaders: imageHeaders,
                    ),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.all(20),
                  child: TextField(
                    decoration: InputDecoration(
                      hintText: 'Entrez votre email',
                      suffixIcon: IconButton(
                        icon: const Icon(Icons.send, color: Colors.teal),
                        onPressed: () {
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              content: Text("Rapport envoyé à l'adresse indiquée !"),
                            ),
                          );
                        },
                      ),
                      border: const OutlineInputBorder(),
                    ),
                  ),
                ),
              ],
            ),
    );
  }
}
