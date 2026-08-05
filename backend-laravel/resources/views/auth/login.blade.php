<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Sign in</title>
    <style>
        /* Same palette as the visualiser this leads to. */
        :root {
            --bg: #14161a; --panel: #1c1f26; --line: #2b3039;
            --ink: #e6e9ef; --dim: #98a1b0; --none: #f85149;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0; min-height: 100vh; background: var(--bg); color: var(--ink);
            font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
            display: flex; align-items: center; justify-content: center; padding: 20px;
        }
        form {
            background: var(--panel); border: 1px solid var(--line);
            border-radius: 8px; padding: 20px; width: 100%; max-width: 340px;
        }
        h1 { font-size: 15px; margin: 0 0 16px; }
        label { display: block; margin-bottom: 12px; color: var(--dim); font-size: 13px; }
        input[type=email], input[type=password] {
            display: block; width: 100%; margin-top: 4px; font: inherit;
            background: #0d1117; color: var(--ink);
            border: 1px solid var(--line); border-radius: 5px; padding: 8px;
        }
        .row { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; }
        .row label { margin: 0; }
        button {
            font: inherit; background: #238636; color: #fff; border: 0;
            border-radius: 5px; padding: 9px 16px; cursor: pointer; width: 100%;
        }
        .err { color: var(--none); margin: 0 0 12px; font-size: 13px; }
    </style>
</head>
<body>
<form method="POST" action="{{ route('login.store') }}">
    @csrf
    <h1>RCU visualiser</h1>

    @if ($errors->any())
        <p class="err">{{ $errors->first() }}</p>
    @endif

    <label>
        Email
        <input type="email" name="email" value="{{ old('email') }}"
               required autofocus autocomplete="username">
    </label>

    <label>
        Password
        <input type="password" name="password" required
               autocomplete="current-password">
    </label>

    <div class="row">
        <input type="checkbox" name="remember" id="remember" value="1">
        <label for="remember">Stay signed in</label>
    </div>

    <button type="submit">Sign in</button>
</form>
</body>
</html>
