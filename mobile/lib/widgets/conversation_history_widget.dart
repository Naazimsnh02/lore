/// Scrollable conversation history for VoiceMode.
///
/// Displays user and assistant messages in a chat-like thread with
/// branch depth indicators and timestamp labels.
///
/// Requirements 1.2, 24.5.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../models/models.dart';
import '../providers/app_providers.dart';

/// Shows the conversation thread between user and assistant.
class ConversationHistoryWidget extends ConsumerStatefulWidget {
  const ConversationHistoryWidget({super.key});

  @override
  ConsumerState<ConversationHistoryWidget> createState() =>
      _ConversationHistoryWidgetState();
}

class _ConversationHistoryWidgetState
    extends ConsumerState<ConversationHistoryWidget> {
  final ScrollController _scrollController = ScrollController();

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final messages = ref.watch(sessionProvider).conversationHistory;

    // Auto-scroll when new messages arrive
    ref.listen<SessionState>(sessionProvider, (prev, next) {
      if ((prev?.conversationHistory.length ?? 0) <
          next.conversationHistory.length) {
        _scrollToBottom();
      }
    });

    if (messages.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.chat_bubble_outline, color: Colors.white24, size: 48),
              SizedBox(height: 12),
              Text(
                'Start speaking to begin\nyour documentary',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: Colors.white38,
                  fontSize: 15,
                  height: 1.4,
                ),
              ),
            ],
          ),
        ),
      );
    }

    return ListView.builder(
      controller: _scrollController,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      itemCount: messages.length,
      itemBuilder: (context, index) {
        final message = messages[index];
        final prevMessage = index > 0 ? messages[index - 1] : null;

        // Show a branch indicator when depth changes
        final showBranchIndicator = prevMessage != null &&
            message.branchDepth != prevMessage.branchDepth;

        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (showBranchIndicator)
              _BranchIndicator(
                entering: message.branchDepth > prevMessage!.branchDepth,
                depth: message.branchDepth,
                topic: message.topic,
              ),
            _ConversationBubble(message: message),
          ],
        );
      },
    );
  }
}

/// A single conversation bubble — user messages on the right, assistant on left.
class _ConversationBubble extends StatelessWidget {
  final ConversationMessage message;

  const _ConversationBubble({required this.message});

  @override
  Widget build(BuildContext context) {
    final isUser = message.role == ConversationRole.user;
    final timeLabel = DateFormat.Hm().format(message.timestamp);

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        mainAxisAlignment:
            isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          if (!isUser) ...[
            _AvatarDot(color: Colors.white38),
            const SizedBox(width: 8),
          ],
          Flexible(
            child: Container(
              constraints: BoxConstraints(
                maxWidth: MediaQuery.of(context).size.width * 0.75,
              ),
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: isUser
                    ? Colors.greenAccent.withAlpha(30)
                    : Colors.white.withAlpha(15),
                borderRadius: BorderRadius.only(
                  topLeft: const Radius.circular(16),
                  topRight: const Radius.circular(16),
                  bottomLeft: Radius.circular(isUser ? 16 : 4),
                  bottomRight: Radius.circular(isUser ? 4 : 16),
                ),
                border: Border.all(
                  color: isUser
                      ? Colors.greenAccent.withAlpha(60)
                      : Colors.white.withAlpha(20),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (message.topic != null && message.topic!.isNotEmpty) ...[
                    Text(
                      message.topic!,
                      style: TextStyle(
                        color: isUser
                            ? Colors.greenAccent.withAlpha(180)
                            : Colors.white54,
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 0.5,
                      ),
                    ),
                    const SizedBox(height: 4),
                  ],
                  Text(
                    message.text,
                    style: TextStyle(
                      color: isUser ? Colors.greenAccent : Colors.white70,
                      fontSize: 14,
                      height: 1.4,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        timeLabel,
                        style: const TextStyle(
                          color: Colors.white24,
                          fontSize: 10,
                        ),
                      ),
                      if (message.branchDepth > 0) ...[
                        const SizedBox(width: 6),
                        Icon(
                          Icons.account_tree_outlined,
                          color: Colors.white24,
                          size: 10,
                        ),
                        const SizedBox(width: 2),
                        Text(
                          'Depth ${message.branchDepth}',
                          style: const TextStyle(
                            color: Colors.white24,
                            fontSize: 10,
                          ),
                        ),
                      ],
                    ],
                  ),
                ],
              ),
            ),
          ),
          if (isUser) ...[
            const SizedBox(width: 8),
            _AvatarDot(color: Colors.greenAccent.withAlpha(150)),
          ],
        ],
      ),
    );
  }
}

/// Small coloured dot used as a conversation avatar indicator.
class _AvatarDot extends StatelessWidget {
  final Color color;
  const _AvatarDot({required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 8,
      height: 8,
      decoration: BoxDecoration(shape: BoxShape.circle, color: color),
    );
  }
}

/// Visual indicator shown when entering or exiting a branch documentary.
class _BranchIndicator extends StatelessWidget {
  final bool entering;
  final int depth;
  final String? topic;

  const _BranchIndicator({
    required this.entering,
    required this.depth,
    this.topic,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        children: [
          Expanded(
            child: Container(
              height: 1,
              color: Colors.deepPurpleAccent.withAlpha(60),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  entering
                      ? Icons.subdirectory_arrow_right
                      : Icons.subdirectory_arrow_left,
                  color: Colors.deepPurpleAccent.withAlpha(150),
                  size: 14,
                ),
                const SizedBox(width: 6),
                Text(
                  entering
                      ? 'Branch $depth${topic != null ? ': $topic' : ''}'
                      : 'Returning to depth $depth',
                  style: TextStyle(
                    color: Colors.deepPurpleAccent.withAlpha(150),
                    fontSize: 11,
                    fontStyle: FontStyle.italic,
                  ),
                ),
              ],
            ),
          ),
          Expanded(
            child: Container(
              height: 1,
              color: Colors.deepPurpleAccent.withAlpha(60),
            ),
          ),
        ],
      ),
    );
  }
}
