# XMPP MCP — E2E Tool Coverage

Every `mcp__xmpp__*` tool, exercised live against the bundled **Openfire** lab
(`start-lab.py` → `127.0.0.1:5222`, bot `bot@xmpp.test`) as a real MCP client,
plus the in-process `pytest -m docker` suite. This records what each tool does
on a live server and the Openfire-specific behaviours to expect.

- **Lab:** Openfire 4.8.1, REST API enabled; users `admin/alice/bob/bot/carol`;
  rooms `r1/r2/r3` on `conference.xmpp.test`; pubsub on `pubsub.xmpp.test`.
- **Status:** ✅ works · ⚠️ documented limitation (server-side, not a tool bug).

## Openfire-lab constraints (see CLAUDE.md for detail)
- `mam_query` — Openfire Monitoring 2.6.1 silently drops MUC MAM; the IQ gets no
  `<result>`/`<fin>`. Use the ejabberd lab for MAM; on Openfire use
  `search_messages` (in-memory buffer). Tool raises a clean ToolError.
- `list_security_labels` — needs an XEP-0258 catalog (Isode M-Link). The lab has
  none, so the server returns `501 feature-not-implemented`; tool surfaces it
  cleanly.
- `pubsub_get/set_subscription_options(defaults=True)` — Openfire doesn't
  implement per-node defaults (`400 bad-request`).

## Messaging (3)
| Tool | Status | Notes |
|---|---|---|
| `send_message` | ✅ | 1:1 delivered (offline-stored when recipient absent, flushed on reconnect). |
| `get_recent_messages` | ✅ | Drains the inbound buffer (1:1 chat + MUC echo). |
| `search_messages` | ✅ | Non-destructive; filters by `query`/`room`/`participant`/`since`. |

## MUC (4)
| Tool | Status | Notes |
|---|---|---|
| `join_room` | ✅ | Joins under the configured `XMPP_NICK`; returns occupants. |
| `send_room_message` | ✅ | Reflected back into the inbox by the room. |
| `list_room_occupants` | ✅ | Role + affiliation per occupant. |
| `leave_room` | ✅ | |

## Presence / Roster (4)
| Tool | Status | Notes |
|---|---|---|
| `set_presence` | ✅ | show + status text. |
| `get_roster` | ✅ | Reflects add/remove. |
| `add_contact` | ✅ | Adds + sends subscription request. |
| `remove_contact` | ✅ | |

## Discovery / Security labels (2)
| Tool | Status | Notes |
|---|---|---|
| `discover_features` | ✅ | Server/entity disco; reports `supports_security_labels`. |
| `list_security_labels` | ⚠️ | `501` on this lab (no XEP-0258 catalog). |

## MAM (1)
| Tool | Status | Notes |
|---|---|---|
| `mam_query` | ⚠️ | Times out on Openfire (works on the ejabberd lab). |

## Pubsub (23) — full lifecycle on `pubsub.xmpp.test`
| Tool | Status | Notes |
|---|---|---|
| `pubsub_list_nodes` | ✅ | |
| `pubsub_create_node` | ✅ | `config_values` applied (e.g. persist_items, max_items). |
| `pubsub_delete_node` | ✅ | |
| `pubsub_get_node_config` | ✅ | XEP-0004 config form. |
| `pubsub_configure_node` | ✅ | |
| `pubsub_publish_form_template` | ✅ | |
| `pubsub_submit_form` | ✅ | |
| `pubsub_read_forms` | ✅ | Parses form items. |
| `pubsub_get_item` | ✅ | Auto-detects form vs raw payload. |
| `pubsub_publish_raw` | ✅ | |
| `pubsub_get_items_raw` | ✅ | |
| `pubsub_retract_item` | ✅ | |
| `pubsub_purge_node` | ✅ | |
| `pubsub_subscribe` | ✅ | Returns subid. |
| `pubsub_unsubscribe` | ✅ | Pass `subid` (Openfire requires it). |
| `pubsub_list_subscriptions` | ✅ | |
| `pubsub_list_node_subscriptions` | ✅ | Owner view. |
| `pubsub_get_subscription_options` | ✅ | Auto-resolves the bot's subid; `subid` arg also accepted. `defaults=True` ⚠️ on Openfire. |
| `pubsub_set_subscription_options` | ✅ | Same subid handling. |
| `pubsub_list_my_affiliations` | ✅ | |
| `pubsub_list_node_affiliations` | ✅ | Owner view. |
| `pubsub_set_affiliations` | ✅ | |
| `pubsub_get_recent_events` | ✅ | Drains buffered publish/retract events; `kind`/`node` filters work. |

> **Openfire `subid-required`:** Openfire issues a subid on subscribe and then
> rejects subscription-options/unsubscribe requests that omit it. The tools
> auto-resolve the bot's own subid (and accept an explicit `subid`); servers
> that use bare subscriptions (ejabberd/Prosody/M-Link) ignore it. Covered by
> `tests/integration/test_pubsub_e2e.py::test_get_set_subscription_options_with_subid`.

## Openfire admin — of_* (7)
| Tool | Status | Notes |
|---|---|---|
| `of_list_users` | ✅ | |
| `of_get_user` | ✅ | |
| `of_create_user` | ✅ | |
| `of_delete_user` | ✅ | |
| `of_list_rooms` | ✅ | |
| `of_create_room` | ✅ | |
| `of_add_group_member` | ⚠️ | Requires a pre-existing group; clean ToolError otherwise. (This restapi build also 500s on `/groups` and `/sessions`.) |

## Summary
44 tools: **41 ✅**, **3 ⚠️** (`list_security_labels`, `mam_query`,
`of_add_group_member` — all server-side limitations, not tool bugs). Error paths
surface clean `ToolError`s with the underlying IQ/HTTP error; no leaked
stacktraces.
