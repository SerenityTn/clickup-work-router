(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const { React } = SDK;
  const { useEffect, useState } = SDK.hooks;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button } = SDK.components;
  const h = React.createElement;

  function Detail({ label, value }) {
    return h("div", { className: "space-y-1" },
      h("p", { className: "text-xs uppercase tracking-wide text-muted-foreground" }, label),
      h("p", { className: "text-sm font-medium break-words" }, value || "Not set")
    );
  }

  function CredentialState({ label, present }) {
    return h("li", { className: "flex items-center justify-between gap-3 rounded-md border px-3 py-2" },
      h("span", { className: "text-sm" }, label),
      h(Badge, { variant: present ? "default" : "outline" }, present ? "available" : "missing")
    );
  }

  function WorkspaceCard({ workspace }) {
    const secrets = workspace.secrets || {};
    const title = workspace.company_name || workspace.name || "Workspace";
    return h(Card, null,
      h(CardHeader, { className: "space-y-3" },
        h("div", { className: "flex flex-wrap items-start justify-between gap-3" },
          h("div", null,
            h(CardTitle, null, title),
            h("p", { className: "mt-1 text-sm text-muted-foreground" }, workspace.name || "Unnamed workspace")
          ),
          h("div", { className: "flex flex-wrap gap-2" },
            h(Badge, { variant: workspace.enabled ? "default" : "outline" }, workspace.enabled ? "enabled" : "disabled"),
            h(Badge, { variant: workspace.dry_run ? "secondary" : "destructive" }, workspace.dry_run ? "dry run" : "writes enabled")
          )
        )
      ),
      h(CardContent, { className: "space-y-5" },
        h("div", { className: "grid gap-4 sm:grid-cols-2 lg:grid-cols-4" },
          h(Detail, { label: "Source", value: workspace.source_system }),
          h(Detail, { label: "Kanban board", value: workspace.kanban_board }),
          h(Detail, { label: "Assignment", value: workspace.assignment_source }),
          h(Detail, { label: "Webhook path", value: workspace.webhook_path })
        ),
        h("div", { className: "grid gap-4 lg:grid-cols-2" },
          h("div", { className: "space-y-2" },
            h("h4", { className: "text-sm font-semibold" }, "Routing policy"),
            h("div", { className: "grid gap-3 sm:grid-cols-2" },
              h(Detail, { label: "Dry run", value: workspace.dry_run ? "Enforced" : "Disabled" }),
              h(Detail, { label: "Trigger status", value: workspace.status_on_trigger }),
              h(Detail, { label: "Completion status", value: workspace.complete_status }),
              h(Detail, { label: "Dispatch backend", value: workspace.dispatch_backend })
            )
          ),
          h("div", { className: "space-y-2" },
            h("h4", { className: "text-sm font-semibold" }, "Credentials"),
            h("ul", { className: "space-y-2" },
              h(CredentialState, { label: "ClickUp team ID", present: Boolean(secrets.clickup_team_id) }),
              h(CredentialState, { label: "ClickUp token", present: Boolean(secrets.clickup_token) }),
              h(CredentialState, { label: "Webhook secret", present: Boolean(secrets.webhook_secret) })
            )
          )
        )
      )
    );
  }

  function EventCard({ event }) {
    const title = event.action || event.type || event.event || "Routing event";
    const summary = event.summary || event.message || event.task_name || event.task_id || "No additional details.";
    return h("li", { className: "rounded-md border px-4 py-3" },
      h("div", { className: "flex flex-wrap items-center justify-between gap-2" },
        h("p", { className: "text-sm font-medium" }, title),
        event.time ? h("time", { className: "text-xs text-muted-foreground" }, event.time) : null
      ),
      h("p", { className: "mt-1 text-sm text-muted-foreground" }, summary)
    );
  }

  function RouterPage() {
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const [refreshKey, setRefreshKey] = useState(0);

    useEffect(function () {
      let active = true;
      setError("");
      SDK.fetchJSON("/api/plugins/clickup-work-router/status")
        .then(function (result) { if (active) setData(result); })
        .catch(function (err) { if (active) setError(String(err)); });
      return function () { active = false; };
    }, [refreshKey]);

    if (error) {
      return h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "ClickUp Router unavailable")),
        h(CardContent, { className: "space-y-3" },
          h("p", { className: "text-sm text-destructive" }, error),
          h(Button, { onClick: function () { setRefreshKey(function (n) { return n + 1; }); } }, "Retry")
        )
      );
    }

    if (!data) {
      return h("p", { className: "text-sm text-muted-foreground" }, "Loading ClickUp Router status…");
    }

    const workspaces = Object.values(data.workspaces || {});
    const events = data.recent_events || [];
    return h("div", { className: "space-y-4" },
      h("div", { className: "flex items-center justify-between gap-4" },
        h("div", null,
          h("h2", { className: "text-xl font-semibold" }, "ClickUp Work Router"),
          h("p", { className: "text-sm text-muted-foreground" }, "ClickUp remains the customer-work source; Hermes Kanban receives selected execution work.")),
        h(Badge, null, data.service && data.service.ok ? "healthy" : "unknown")
      ),
      h("section", { className: "space-y-3" },
        h("div", { className: "flex items-center justify-between" },
          h("h3", { className: "text-base font-semibold" }, "Workspaces"),
          h("span", { className: "text-xs text-muted-foreground" }, workspaces.length + (workspaces.length === 1 ? " workspace" : " workspaces"))
        ),
        workspaces.length
          ? h("div", { className: "space-y-3" }, workspaces.map(function (workspace) { return h(WorkspaceCard, { key: workspace.name, workspace: workspace }); }))
          : h(Card, null, h(CardContent, { className: "py-6 text-sm text-muted-foreground" }, "No configured workspaces."))
      ),
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Recent routing events")),
        h(CardContent, null,
          events.length
            ? h("ul", { className: "space-y-2" }, events.map(function (event, index) { return h(EventCard, { key: event.id || event.time || index, event: event }); }))
            : h("p", { className: "text-sm text-muted-foreground" }, "No recent routing events.")
        )
      )
    );
  }

  window.__HERMES_PLUGINS__.register("clickup-work-router", RouterPage);
})();
