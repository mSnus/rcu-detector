<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>@yield('title', 'RCU visualiser')</title>
    <style>
        :root {
            --bg: #14161a; --panel: #1c1f26; --line: #2b3039;
            --ink: #e6e9ef; --dim: #98a1b0;
            --high: #3fb950; --medium: #d29922; --low: #db6d28; --none: #f85149;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0; background: var(--bg); color: var(--ink);
            font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
        }
        header {
            padding: 12px 20px; border-bottom: 1px solid var(--line);
            display: flex; gap: 16px; align-items: baseline; flex-wrap: wrap;
        }
        header h1 { font-size: 15px; margin: 0; font-weight: 600; }
        a { color: #58a6ff; text-decoration: none; }
        a:hover { text-decoration: underline; }
        main { padding: 20px; max-width: 1600px; }
        .panel {
            background: var(--panel); border: 1px solid var(--line);
            border-radius: 6px; padding: 14px; margin-bottom: 16px;
        }
        .panel h2 { font-size: 13px; margin: 0 0 10px; color: var(--dim);
                    text-transform: uppercase; letter-spacing: .06em; }
        table { border-collapse: collapse; width: 100%; }
        th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); }
        th { color: var(--dim); font-weight: 600; white-space: nowrap; }
        td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
        .scroll { overflow-x: auto; }
        .tag {
            display: inline-block; padding: 1px 7px; border-radius: 10px;
            font-size: 12px; font-weight: 600; color: #0d1117;
        }
        .tag.high { background: var(--high); }
        .tag.medium { background: var(--medium); }
        .tag.low { background: var(--low); }
        .tag.none { background: var(--none); }
        .dim { color: var(--dim); }
        pre {
            background: #0d1117; border: 1px solid var(--line); border-radius: 6px;
            padding: 12px; overflow: auto; max-height: 460px; font-size: 12px;
        }
        img.shot { max-width: 100%; border: 1px solid var(--line); border-radius: 4px; }
        .row { display: flex; gap: 16px; flex-wrap: wrap; }
        .row > * { flex: 1 1 320px; min-width: 0; }
        .filters { display: flex; gap: 10px; }
        .filters a.on { color: var(--ink); font-weight: 600; }
        button, input[type=file] { font: inherit; }
        button {
            background: #238636; color: #fff; border: 0; border-radius: 5px;
            padding: 6px 14px; cursor: pointer;
        }
    </style>
</head>
<body>
<header>
    <h1><a href="{{ route('admin.rcu.index') }}">RCU visualiser</a></h1>
    <nav class="filters">
        <a href="{{ route('admin.rcu.index') }}">queries</a>
        <a href="{{ route('admin.rcu.catalog') }}">catalog</a>
    </nav>
    @isset($health)
        <span class="dim">
            service:
            <strong style="color: {{ ($health['status'] ?? '') === 'ok' ? 'var(--high)' : 'var(--none)' }}">
                {{ $health['status'] ?? 'unknown' }}
            </strong>
            @if(! empty($health['index_records']))
                &middot; {{ $health['index_records'] }} records / {{ $health['index_docs'] }} docs
            @endif
            @if(! empty($health['error']))
                &middot; {{ $health['error'] }}
            @endif
        </span>
    @endisset
</header>
<main>@yield('content')</main>
</body>
</html>
