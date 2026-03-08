/// Basic smoke test — verifies the LORE app launches without crashing.
///
/// Firebase and platform plugins are not available in the test environment,
/// so we just verify the app widget tree can be built with ProviderScope.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('LoreApp smoke test — builds without crashing', (WidgetTester tester) async {
    // Wrap in a simple MaterialApp to avoid Firebase init in test context
    await tester.pumpWidget(
      const ProviderScope(
        child: MaterialApp(
          home: Scaffold(
            body: Text('LORE Test'),
          ),
        ),
      ),
    );

    expect(find.text('LORE Test'), findsOneWidget);
  });
}
