---
name: working-with-asana
description: Use when an Asana task link is provided, or before commenting on, updating, or creating any Asana task.
---

# Working With Asana

## Reading

For an Asana task link, read the task's `html_notes`, viewing every note image with its surrounding text for context. Then read every task comment.

This is the default context-gathering behavior. Do not read other task fields unless the user explicitly requests them.

## Writing

Asana is client-visible, and a post cannot be unsent. Every write is **draft-then-confirm**: post only the content the user has approved, as written.

Writes include comments, description and custom field edits, status/assignee/due-date changes, task and subtask creation, and attachments.

1. Print the exact content in chat, in full, and name the target task.
2. Ask for approval, then stop. This ends your turn.
3. Post only after the user's next message approves that content.

Approval is a user message responding to the content you printed. These are **not** approval:

- Choosing an option in an `AskUserQuestion`, including an option that says to post. That selects a plan; the content is still unwritten and unseen.
- Approval of an earlier post. Each write is approved on its own.
- The user supplying the task link, assigning the task, or asking for work on it.

When the user asks for changes, print the revised content in full and ask again.
