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
            --best: #d4af37;
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
        /* One column of photographs per model: an original and a compatible
           copy are different products with different pictures, and seeing them
           side by side is how you tell which one is in your hand. */
        .cand .shots { display: flex; flex-direction: column; gap: 6px; flex: none; }
        .cand .meta { flex: 1; min-width: 0; }
        .cand .name { font-weight: 600; word-break: break-all; }
        .cand .name a { color: var(--ink); text-decoration: underline; }
        .cand .name a:hover { color: var(--high); }
        /* The top group, marked out rather than merely first. On a phone the
           list scrolls and "first" stops being visible; a border is still
           visible with the heading off screen. */
        .cand.best {
            border: 2px solid var(--best); border-radius: 8px;
            padding: 10px; margin-bottom: 10px;
        }
        .also {
            font-size: 12px; color: var(--dim); text-transform: uppercase;
            letter-spacing: .06em; margin: 4px 0 2px;
        }
        .variant { margin-top: 8px; }
        .variant + .variant { border-top: 1px dashed var(--line); padding-top: 8px; }
        .variant a { color: var(--ink); }
        .cand .nums { color: var(--dim); font-size: 12px; }
        .cand button { margin-top: 6px; }
        /* Tapping a candidate opens it beside the photograph that was sent.
           A bigger crop on its own does not answer the question the user is
           actually asking, which is "is this the same remote as mine". */
        .cand img { cursor: zoom-in; }
        #lightbox {
            position: fixed; inset: 0; z-index: 10; background: rgba(0,0,0,.92);
            display: flex; flex-direction: column; padding: 12px; gap: 8px;
        }
        #lightbox[hidden] { display: none; }
        #lightbox .pair {
            flex: 1; min-height: 0; display: flex; gap: 12px; justify-content: center;
        }
        #lightbox figure {
            margin: 0; flex: 1; min-width: 0; display: flex;
            flex-direction: column; align-items: center; gap: 6px;
        }
        #lightbox img {
            min-height: 0; max-height: 100%; max-width: 100%;
            object-fit: contain; border-radius: 6px;
        }
        #lightbox figcaption { font-size: 12px; color: var(--dim); }
        #lightbox .close { align-self: center; }
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

<div id="lightbox" hidden>
    <div class="pair">
        <figure>
            <img id="lbCand" alt="">
            <figcaption id="lbName">catalog</figcaption>
        </figure>
        <figure>
            <img id="lbMine" alt="">
            <figcaption>your photo</figcaption>
        </figure>
    </div>
    <button class="close" id="lbClose">Close</button>
</div>

<script>
const $ = id => document.getElementById(id);
const MAX_KB = @json($maxUploadKb);
/* Presentation only -- the API returns the same payload either way. See
   `try_simple` in config/rcu.php for what is hidden and why. */
const SIMPLE = @json($simple);
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

/* Delegated, because the candidate rows are rebuilt on every query and a
   listener bound to the images would be lost with them. */
$('results').addEventListener('click', e => {
    const img = e.target.closest('img[data-big]');
    if (!img) return;
    $('lbCand').src = img.dataset.big;
    $('lbName').textContent = img.closest('.cand')?.querySelector('.name')?.textContent || 'catalog';
    $('lbMine').src = $('previewImg').src;
    $('lightbox').hidden = false;
});

function closeLightbox() { $('lightbox').hidden = true; }
$('lbClose').addEventListener('click', closeLightbox);
$('lightbox').addEventListener('click', e => {
    // Anywhere outside the two images, which is the gesture people try first.
    if (e.target.tagName !== 'IMG') closeLightbox();
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeLightbox();
});

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
    // Says what is happening and how long it may take. The old text explained
    // an implementation detail -- OCR model loading -- which tells a user
    // nothing they can act on and reads like a fault.
    say('Identifying&hellip; this takes a few seconds.');

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
        + (data.hint ? `<span class="dim">${esc(hintText(data.hint))}</span>` : '')
        + (SIMPLE ? '' : `<div class="stats">`
        + `${ex.button_count ?? '—'} buttons`
        + ` &middot; brand <code>${esc(ex.brand ?? '—')}</code>`
        + ` &middot; model <code>${esc(ex.model_code ?? '—')}</code>`
        + ` &middot; ${data.latency_ms ?? '—'} ms`
        + ` &middot; <span class="dim">${esc(data.request_id ?? '')}</span>`
        + `</div>`));

    const cands = data.candidates || [];
    const box = $('results');

    if (!cands.length) {
        box.innerHTML = '<p>No candidates. Nothing in the catalog resembles this.</p>'
            + feedbackOnlyButton();
        box.hidden = false;
        wireFeedback();
        return;
    }

    box.innerHTML = groupByModel(cands).map((g, i) =>
        // The heading goes before the second group, not above a section that
        // may be empty: with one candidate there is nothing else possible and
        // an "Also possible:" with nothing under it reads as a failure to load.
        (i === 1 ? '<div class="also">Also possible:</div>' : '') + renderGroup(g, i === 0)
    ).join('') + feedbackOnlyButton();

    box.hidden = false;
    wireFeedback();
}

/* One remote can be in the catalogue several times over -- an original and a
   compatible copy are separate products with separate photographs, and both
   are correct answers to "what is this". Listing them as unrelated candidates
   makes a right answer look like an uncertain one.

   Grouped on the model code, which is the catalogue's own identifier for a
   remote and, since the codes came from the titles, present on most records.
   A record with no code is its own group: grouping on the title instead would
   merge remotes that merely share a description, which is most of them. */
function groupByModel(cands) {
    const groups = [];
    const byKey = new Map();

    for (const c of cands) {
        const cat = c.catalog || {};
        const key = cat.model_code || c.model_code || `id:${c.record_id}`;
        let g = byKey.get(key);
        if (!g) {
            // Candidates arrive sorted, so the first member of a group is its
            // best and the groups come out in score order for free.
            g = {key, code: cat.model_code || c.model_code || null, members: []};
            byKey.set(key, g);
            groups.push(g);
        }
        g.members.push(c);
    }
    return groups;
}

function renderGroup(g, best) {
    const top = g.members[0];
    const cat = top.catalog || {};
    /* The catalogue's own title first. brand/model_code are what the
       *extractor* read off a photograph and are null on most records, so
       preferring them left every row named by its record_id -- a filename
       stem, which names nothing a person recognises. */
    const heading = g.code
        || cat.title
        || [cat.brand || top.brand].filter(Boolean).join(' ')
        || top.record_id;

    const link = cat.item_url
        ? `<a href="${esc(cat.item_url)}" target="_blank" rel="noopener">${esc(heading)}</a>`
        : esc(heading);

    const photos = g.members.map(m => {
        const src = PHOTO_URL.replace('__ID__', encodeURIComponent(m.record_id));
        return `<img src="${src}" alt="" loading="lazy" data-big="${src}"
                     title="Tap to enlarge"
                     onerror="this.style.visibility='hidden'">`;
    }).join('');

    const rows = g.members.map(m => {
        const mc = m.catalog || {};
        // The title repeats the code it was parsed out of, and the code is
        // already the heading. Strip it so the variant reads as what it is --
        // "оригинальный", "неоригинальный" -- rather than the same line twice.
        let label = mc.title || m.record_id;
        if (g.code && label.toUpperCase().startsWith(g.code.toUpperCase())) {
            label = label.slice(g.code.length).replace(/^[\s,;-]+/, '');
        }
        const u = mc.item_url
            ? `<a href="${esc(mc.item_url)}" target="_blank" rel="noopener">${esc(label)}</a>`
            : esc(label);
        if (SIMPLE) {
            return `<div class="variant"><div>${u}</div></div>`;
        }
        return `<div class="variant">
            <div>${u}</div>
            <div class="nums">
                score ${m.score?.toFixed(3) ?? '—'}
                &middot; ${m.inliers ?? '—'} inliers
                &middot; ${mc.button_count ?? '—'} buttons
                ${orientationNote(m.orientation)}
                &middot; <span class="dim">${esc(m.record_id)}</span>
            </div>
            <button data-choose="${esc(m.record_id)}">That's it</button>
        </div>`;
    }).join('');

    return `<div class="cand${best ? ' best' : ''}">
        <div class="shots">${photos}</div>
        <div class="meta">
            <div class="name">${link}</div>
            ${rows}
        </div>
    </div>`;
}

/* The service returns a hint as a token, because it is an API and an enum is
   what an API should return. Turning it into a sentence is the client's job,
   and printing the token instead -- "hint: photograph_back" -- tells the user
   nothing and reads like a leaked internal.

   It also has to say *another photo*, not a second one: there is no two-image
   flow, and the wording implied there was. */
const HINTS = {
    // The model code is the highest-precision signal the matcher has, and this
    // query carried none. On most remotes it is printed on the back, but on
    // plenty -- this Samsung included -- it is on the front near the bottom.
    photograph_back: 'No model code was readable. Try another photo showing the '
        + 'printed code — usually on the back, sometimes below the buttons.',
    reshoot: 'Try another photo: fill the frame with the remote, straight on, '
        + 'in even light and without a strong shadow beside it.',
    // `none` on a photograph the extractor read perfectly well. Telling that
    // user to reshoot is both wrong and useless: a Technika DTV1 came back at
    // 0.299 from a crop scoring 0.943 with 21 buttons, and was blamed for the
    // picture. The catalogue simply does not hold it.
    not_in_catalog: 'We read your remote clearly, but nothing in the catalogue '
        + 'matches it — we probably do not stock this one yet.',
    // Not a weak answer -- two answers. Every wrong result in the calibration
    // looked like this, and about a quarter of ties are the same remote listed
    // twice, which the grouping above already merges into one card.
    tied: 'Two records match equally well. Compare them below — they may be '
        + 'the same remote listed twice, or two that look alike.',
};

function hintText(h) {
    return HINTS[h] || h;
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
       which no positive pick ever does. Still dropped in simple mode: with the
       scores hidden the page is no longer asking a question, and a lone
       negative button next to no positive one reads as an error control. */
    if (SIMPLE) return '';
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
