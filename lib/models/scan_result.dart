class ScanResult {
  final String label;
  final String confidence;
  final String imageUrl;
  final String fullImageUrl;
  final String triStatus;
  final int? points;

  // Champs ensemble (raycash-pro V4) — toujours présents, valeurs neutres
  // pour les vieilles réponses sans ces champs.
  final bool accepted;
  final bool uncertain;
  final double entropy;
  final double disagreement;
  final List<String> perModelTop;
  final int nViews;
  final String? hint;

  const ScanResult({
    required this.label,
    required this.confidence,
    required this.imageUrl,
    required this.fullImageUrl,
    required this.triStatus,
    this.points,
    this.accepted = true,
    this.uncertain = false,
    this.entropy = 0.0,
    this.disagreement = 0.0,
    this.perModelTop = const [],
    this.nViews = 1,
    this.hint,
  });

  factory ScanResult.fromServer(Map<String, dynamic> json, String serverBase) {
    final imageUrl = json['image_url'] as String? ?? '';
    final urls = (json['image_urls'] as List?)?.cast<String>() ?? [imageUrl];
    final firstUrl = urls.isNotEmpty ? urls.first : '';
    return ScanResult(
      label: json['label'] as String? ?? 'Inconnu',
      confidence: json['confidence'] as String? ?? '',
      imageUrl: firstUrl,
      fullImageUrl: firstUrl.isEmpty ? '' : '$serverBase$firstUrl',
      triStatus: json['tri_status'] as String? ?? 'NON_RECYCLABLE',
      points: (json['points'] as num?)?.toInt(),
      accepted: json['accepted'] as bool? ?? true,
      uncertain: json['uncertain'] as bool? ?? false,
      entropy: (json['entropy'] as num?)?.toDouble() ?? 0.0,
      disagreement: (json['disagreement'] as num?)?.toDouble() ?? 0.0,
      perModelTop: (json['per_model_top'] as List?)?.cast<String>() ?? const [],
      nViews: (json['n_views'] as num?)?.toInt() ?? 1,
      hint: json['hint'] as String?,
    );
  }

  Map<String, dynamic> toJson() => {
        'label': label,
        'confidence': confidence,
        'image_url': imageUrl,
        'full_image_url': fullImageUrl,
        'tri_status': triStatus,
        if (points != null) 'points': points,
        'accepted': accepted,
        'uncertain': uncertain,
        if (entropy != 0.0) 'entropy': entropy,
        if (disagreement != 0.0) 'disagreement': disagreement,
        if (perModelTop.isNotEmpty) 'per_model_top': perModelTop,
        'n_views': nViews,
        if (hint != null) 'hint': hint,
      };

  factory ScanResult.fromJson(Map<String, dynamic> json) => ScanResult(
        label: json['label'] as String? ?? 'Inconnu',
        confidence: json['confidence'] as String? ?? '',
        imageUrl: json['image_url'] as String? ?? '',
        fullImageUrl: json['full_image_url'] as String? ?? '',
        triStatus: json['tri_status'] as String? ?? 'NON_RECYCLABLE',
        points: (json['points'] as num?)?.toInt(),
        accepted: json['accepted'] as bool? ?? true,
        uncertain: json['uncertain'] as bool? ?? false,
        entropy: (json['entropy'] as num?)?.toDouble() ?? 0.0,
        disagreement: (json['disagreement'] as num?)?.toDouble() ?? 0.0,
        perModelTop: (json['per_model_top'] as List?)?.cast<String>() ?? const [],
        nViews: (json['n_views'] as num?)?.toInt() ?? 1,
        hint: json['hint'] as String?,
      );
}
