(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const { React } = SDK;
  const { useEffect, useState } = SDK.hooks;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button } = SDK.components;
  const h = React.createElement;

  function JsonBlock({ value }) {
    return h("pre", { className: "max-h-72 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground" }, JSON.stringify(value, null, 2));
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

    const workspaces = data.workspaces || {};
    const workspaceNames = Object.keys(workspaces);
    return h("div", { className: "space-y-4" },
      h("div", { className: "flex items-center justify-between" },
        h("div", null,
          h("h2", { className: "text-xl font-semibold" }, "ClickUp Work Router"),
          h("p", { className: "text-sm text-muted-foreground" }, "ClickUp remains the customer-work source; Hermes Kanban receives selected execution work.")),
        h(Badge, null, data.service && data.service.ok ? "healthy" : "unknown")
      ),
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Workspaces")),
        h(CardContent, null, workspaceNames.length ? h(JsonBlock, { value: workspaces }) : h("p", { className: "text-sm text-muted-foreground" }, "No configured workspaces."))
      ),
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Recent routing events")),
        h(CardContent, null, h(JsonBlock, { value: data.recent_events || [] }))
      )
    );
  }

  window.__HERMES_PLUGINS__.register("clickup-work-router", RouterPage);
})();
