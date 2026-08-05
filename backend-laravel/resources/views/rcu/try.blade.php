<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Identify a remote</title>
    <style>
        /* Same palette as the admin visualiser, laid out for a phone held in
           one hand rather than a desk monitor. */
        :root {
            --bg: #14161a; --panel: #1c1f26; --line: #2b3039;
            --ink: #e6e9ef; --dim: #98a1b0;
            --high: #3fb950; --medium: #d29922; --low: #db6d28; --none: #f85149;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0; background: var(--bg); color: var(--ink);
            font: 15px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
            padding: 16px 16px calc(32px + env(safe-area-inset-bottom));
            max-width: 720px; margin-inline: auto;
        }
        h1 { font-size: 16px; margin: 0 0 4px; }
        p.dim, span.dim { color: var(--dim); }
        p.dim { margin: 0 0 16px; font-size: 13px; }
        .panel {
            background: var(--panel); border: 1px solid var(--line);
            border-radius: 8px; padding: 14px; margin-bottom: 14px;
        }
        /* The capture control is the whole point of the page on a phone, so it
           is a full-width tap target rather than a bare file input. */
        label.shoot {
            display: block; text-align: center; cursor: pointer;
            background: #238636; color: #fff; border-radius: 8px;
            padding: 16px; font-weight: 600;
        }
        label.shoot input { display: none; }
        /* Second control, for a photo already on the device. Deliberately
           quieter than the camera: on a phone that is still the main path. */
        label.shoot.alt {
            background: #30363d; color: var(--ink);
            border: 1px solid var(--line); margin-top: 8px; font-weight: 500;
        }
        button {
            font: inherit; background: #30363d; color: var(--ink);
            border: 1px solid var(--line); border-radius: 6px;
            padding: 8px 14px; cursor: pointer;
        }
        button.primary { background: #238636; color: #fff; border-color: #238636; }
        button:disabled { opacity: .5; cursor: default; }
        img.shot { max-width: 100%; border-radius: 6px; display: block; }
        .tag {
            display: inline-block; padding: 1px 8px; border-radius: 10px;
            font-size: 12px; font-weight: 600; color: #0d1117;
        }
        .tag.high { background: var(--high); }
        .tag.medium { background: var(--medium); }
        .tag.low { background: var(--low); }
        .tag.none { background: var(--none); }
        .cand {
            display: flex; gap: 12px; align-items: flex-start;
            padding: 10px 0; border-top: 1px solid var(--line);
        }
        .cand:first-child { border-top: 0; }
        /* Catalog crops are tall and narrow. Fixed box, contain, so a row is
           the same height whatever shape the remote is. */
        .cand img {
            width: 64px; height: 104px; object-fit: contain;
            background: #0d1117; border-radius: 4px; flex: none;
        }
        .cand .meta { flex: 1; min-width: 0; }
        .cand .name { font-weight: 600; word-break: break-all; }
        .cand .nums { color: var(--dim); font-size: 12px; }
        .cand button { margin-top: 6px; }
        .err { color: var(--none); }
        .ok { color: var(--high); }
        .stats { font-size: 12px; color: var(--dim); margin-top: 8px; }
        .stats code { color: var(--ink); }
    </style>
</head>
<body>

<h1>Identify a remote</h1>
<p class="dim">
    {{ $catalogSize }} record{{ $catalogSize === 1 ? '' : 's' }} in the catalog.
    @if ($catalogSize < 500)
        That is a sample, not the catalogue &mdash; expect <em>none</em> for most
        real remotes until the full build is loaded.
    @endif
</p>

<div class="panel">
    <label class="shoot">
        Take a photo
        <input type="file" id="photo" accept="image/*" capture="environment">
    </label>
    {{-- Same accept, no `capture`: that attribute is what makes a phone open
         the camera instead of the picker, so dropping it is the whole
         difference between the two controls. --}}
    <label class="shoot alt">
        Upload a photo
        <input type="file" id="upload" accept="image/*">
    </label>
    {{-- Stated before the choice, not after the failure. The limit is the
         server's own, so the two cannot drift. --}}
    <p class="dim" style="margin: 8px 0 0; text-align: center">
        JPEG, PNG or WebP, up to {{ round($maxUploadKb / 1024, 1) }} MB.
    </p>
    <div id="preview" hidden style="margin-top: 12px">
        <img class="shot" id="previewImg" alt="">
        <div style="margin-top: 10px; display: flex; gap: 8px">
            <button class="primary" id="send">Identify</button>
            <button id="clear">Clear</button>
        </div>
    </div>
</div>

<div id="status" class="panel" hidden></div>
<div id="results" class="panel" hidden></div>

<script>
const $ = id => document.getElementById(id);
const MAX_KB = @json($maxUploadKb);
/* A path, not an absolute URL. Behind a TLS-terminating proxy Laravel builds
   absolute URLs with the scheme it thinks it is serving, which is http unless
   TrustProxies is configured for that proxy -- and every one of these images
   would then be blocked as mixed content on an https page. A path inherits the
   scheme the page was actually loaded over and cannot get this wrong. */
const PHOTO_URL = @json(route('rcu.try.photo', ['recordId' => '__ID__'], absolute: false));

let file = null;
let requestId = null;

// Both controls feed the same variable, and picking from one clears the other:
// two file inputs each hold their own selection, so without this the stale one
// is what a later `.value` read sees.
function picked(e) {
    file = e.target.files[0] || null;
    if (!file) return;
    for (const id of ['photo', 'upload']) {
        if (id !== e.target.id) $(id).value = '';
    }
    $('previewImg').src = URL.createObjectURL(file);
    $('preview').hidden = false;
    $('results').hidden = true;
    say('');
}

$('photo').addEventListener('change', picked);
$('upload').addEventListener('change', picked);

$('clear').addEventListener('click', () => {
    file = null; requestId = null;
    $('photo').value = '';
    $('upload').value = '';
    $('preview').hidden = true;
    $('results').hidden = true;
    say('');
});

$('send').addEventListener('click', identify);

function say(html, cls = '') {
    const el = $('status');
    el.className = 'panel ' + cls;
    el.innerHTML = html;
    el.hidden = !html;
}

function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g,
        c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function identify() {
    if (!file) return;
    // In MB, because the limit is stated in MB and a number the user cannot
    // compare to the one above it is not a message.
    if (file.size > MAX_KB * 1024) {
        const mb = n => (n / 1024 / 1024).toFixed(1);
        say(`That photo is ${mb(file.size)} MB and the limit is ${mb(MAX_KB * 1024)} MB.`
            + ' <span class="dim">Most phones can send a smaller one from the'
            + ' gallery, or try the camera button.</span>', 'err');
        return;
    }

    $('send').disabled = true;
    say('Identifying&hellip; first request after a restart pays for model loading.');

    const body = new FormData();
    body.append('photo', file);

    let res;
    try {
        // No top_k here: the endpoint takes it from config (rcu.top_k), and a
        // query parameter that silently does nothing is worse than none.
        res = await fetch('/api/identify', {
            method: 'POST', body, headers: {'Accept': 'application/json'},
        });
    } catch (e) {
        // Never reached the application at all.
        say('Could not reach the server: ' + esc(e.message)
            + ' <span class="dim">Check the connection and try again.</span>', 'err');
        $('send').disabled = false;
        return;
    }

    /* Parsed separately from the request. The errors that matter most here do
       NOT come back as JSON -- nginx answers an oversized body with an HTML
       413 and PHP-FPM an HTML 502 -- and parsing inside the try above reported
       both as "could not reach the server", which is the one thing they are
       not. */
    let data = {};
    try {
        data = await res.json();
    } catch (e) {
        data = {};
    }

    $('send').disabled = false;

    /* A 4xx is a verdict on the photograph and is identical next time; a 5xx
       is about the service. Telling the user to retry the first is how a decode
       bug spent a session disguised as an outage. Each branch says what the
       user can actually do about it -- "an error occurred" is not a message. */
    const retry = ' <span class="dim">This one is worth retrying.</span>';
    const useless = ' <span class="dim">Retrying the same file will not help.</span>';

    if (res.ok) {
        requestId = data.request_id;
        render(data);
        return;
    }

    if (res.status === 413) {
        // The server refused the body outright, before any application code.
        say(`That photo is too large for the server (${(file.size / 1024 / 1024).toFixed(1)} MB).`
            + useless, 'err');
        return;
    }
    if (res.status === 422) {
        say(esc(data.message || 'That image could not be read.')
            + ' <span class="dim">Retrying the same file will not help &mdash;'
            + ' take another photo, closer and better lit.</span>', 'err');
        return;
    }
    if (res.status === 429) {
        say('Too many requests just now.'
            + ' <span class="dim">Wait a few seconds and try again.</span>', 'err');
        return;
    }
    if (res.status === 401 || res.status === 403 || res.status === 419) {
        say('The server rejected the request as unauthorised.'
            + ' <span class="dim">Reload the page and try again.</span>', 'err');
        return;
    }
    if (res.status === 404) {
        say('The identify endpoint is missing (404).'
            + ' <span class="dim">The deployment is wrong, not the photo.</span>', 'err');
        return;
    }
    if (res.status === 503) {
        say(esc(data.message || 'The recognition service is not answering.')
            + retry, 'err');
        return;
    }
    if (res.status >= 500) {
        // 502/504 come from nginx as HTML, so data.message is usually absent
        // and the status is the only thing there is to say.
        say(esc(data.message || `The server failed (${res.status}).`) + retry, 'err');
        return;
    }

    say(esc(data.message || `The request was refused (${res.status}).`) + useless, 'err');
}

function render(data) {
    const ex = data.extracted || {};
    const band = data.confidence || 'none';

    say(`<span class="tag ${esc(band)}">${esc(band)}</span> `
        + (data.hint ? `<span class="dim">hint: ${esc(data.hint)}</span>` : '')
        + `<div class="stats">`
        + `${ex.button_count ?? '—'} buttons`
        + ` &middot; brand <code>${esc(ex.brand ?? '—')}</code>`
        + ` &middot; model <code>${esc(ex.model_code ?? '—')}</code>`
        + ` &middot; ${data.latency_ms ?? '—'} ms`
        + ` &middot; <span class="dim">${esc(data.request_id ?? '')}</span>`
        + `</div>`);

    const cands = data.candidates || [];
    const box = $('results');

    if (!cands.length) {
        box.innerHTML = '<p>No candidates. Nothing in the catalog resembles this.</p>'
            + feedbackOnlyButton();
        box.hidden = false;
        wireFeedback();
        return;
    }

    box.innerHTML = cands.map(c => {
        const cat = c.catalog || {};
        const name = [cat.brand || c.brand, cat.model_code || c.model_code]
            .filter(Boolean).join(' ') || c.record_id;
        return `<div class="cand">
            <img src="${PHOTO_URL.replace('__ID__', encodeURIComponent(c.record_id))}"
                 alt="" loading="lazy" onerror="this.style.visibility='hidden'">
            <div class="meta">
                <div class="name">${esc(name)}</div>
                <div class="nums">
                    score ${c.score?.toFixed(3) ?? '—'}
                    &middot; ${c.inliers ?? '—'} inliers
                    &middot; ${cat.button_count ?? '—'} buttons
                    ${orientationNote(c.orientation)}
                </div>
                <div class="nums">${esc(c.record_id)}</div>
                <button data-choose="${esc(c.record_id)}">That's it</button>
            </div>
        </div>`;
    }).join('') + feedbackOnlyButton();

    box.hidden = false;
    wireFeedback();
}

/* `orientation` is an object, not a string: the two sides flip independently,
   and every query is tried both ways up because a query's orientation
   confidence is not the catalog's. Worth showing -- a match found upside down
   is still a match, but it is the first thing to look at when one is wrong. */
function orientationNote(o) {
    if (!o) return '';
    const notes = [];
    if (o.query_flipped) notes.push('photo read upside down');
    if (o.candidate_flipped) notes.push('record indexed upside down');
    return notes.length ? '&middot; ' + esc(notes.join(', ')) : '';
}

function feedbackOnlyButton() {
    /* "None of these" is the most informative answer the page can collect: it
       says the catalog is missing this remote or the extraction was wrong,
       which no positive pick ever does. */
    return `<div style="margin-top: 12px; border-top: 1px solid var(--line); padding-top: 12px">
        <button data-choose="">None of these</button>
    </div>`;
}

function wireFeedback() {
    $('results').querySelectorAll('[data-choose]').forEach(b => {
        b.addEventListener('click', () => choose(b.dataset.choose, b));
    });
}

async function choose(recordId, btn) {
    if (!requestId) return;
    $('results').querySelectorAll('button').forEach(b => b.disabled = true);

    const payload = recordId
        ? {record_id: recordId}
        : {none_of_these: true};

    try {
        const res = await fetch(`/api/identify/${encodeURIComponent(requestId)}/choose`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        btn.textContent = 'Recorded';
        btn.classList.add('primary');
    } catch (e) {
        btn.textContent = 'Failed: ' + e.message;
        $('results').querySelectorAll('button').forEach(b => b.disabled = false);
    }
}
</script>
</body>
</html>
