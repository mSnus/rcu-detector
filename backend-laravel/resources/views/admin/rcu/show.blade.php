@extends('admin.rcu.layout')
@section('title', 'Query ' . $query->request_id)

@section('content')
    <p><a href="{{ route('admin.rcu.index') }}">&larr; all queries</a></p>

    <div class="panel">
        <h2>{{ $query->request_id }}</h2>
        <p>
            <span class="tag {{ $query->confidence }}">{{ $query->confidence }}</span>
            <span class="dim">
                &middot; {{ $query->latency_ms ?? '—' }} ms
                &middot; {{ $query->bodies_found ?? '—' }} bodies
                @if ($query->model_code_fast_path) &middot; model-code fast path @endif
                @if ($query->hint) &middot; hint: <strong>{{ $query->hint }}</strong> @endif
            </span>
        </p>
    </div>

    <div class="row">
        <div class="panel">
            <h2>Uploaded photo</h2>
            <img class="shot" src="{{ route('admin.rcu.upload', $query->request_id) }}" alt="upload">
        </div>
        <div class="panel">
            <h2>Extraction overlay</h2>
            @if ($hasOverlay)
                {{-- original | fg mask | body detect | rectified | buttons | text --}}
                <img class="shot" src="{{ route('admin.rcu.overlay', $query->request_id) }}" alt="overlay">
            @else
                <p class="dim">
                    No overlay retained. The service keeps these in a small bounded
                    in-memory ring, so only the last few requests have one &mdash;
                    this is expected for anything older, not an error.
                </p>
            @endif
        </div>
    </div>

    <div class="panel">
        <h2>Extracted</h2>
        @if ($query->extracted)
            <table>
                <tr><th>brand</th><td>{{ $query->extracted['brand'] ?? '—' }}</td></tr>
                <tr><th>model code</th><td>{{ $query->extracted['model_code'] ?? '—' }}</td></tr>
                <tr><th>buttons</th><td>{{ $query->extracted['button_count'] ?? '—' }}</td></tr>
                <tr><th>quality</th><td>{{ $query->extracted['quality'] ?? '—' }}</td></tr>
                {{-- Low orientation confidence means the fingerprint may be stored
                     upside down, which corrupts matching silently. Worth seeing. --}}
                <tr><th>orientation conf</th><td>{{ $query->extracted['orientation_conf'] ?? '—' }}</td></tr>
            </table>
        @else
            <p class="dim">No remote was found in this photo.</p>
        @endif
    </div>

    <div class="panel">
        <h2>Candidates</h2>
        <div class="scroll">
            <table>
                <thead>
                <tr>
                    <th>#</th>
                    <th>record</th>
                    <th class="num">score</th>
                    <th class="num">inliers</th>
                    <th>brand</th>
                    <th>model code</th>
                    <th class="num">tier1</th>
                    <th class="num">geometric</th>
                    <th class="num">brand agr.</th>
                    <th class="num">aspect agr.</th>
                    <th class="num">code bonus</th>
                    <th></th>
                </tr>
                </thead>
                <tbody>
                @forelse ($candidates as $i => $c)
                    @php $t = $c['terms'] ?? []; @endphp
                    <tr>
                        <td class="dim">{{ $i + 1 }}</td>
                        <td><strong>{{ $c['record_id'] ?? '—' }}</strong></td>
                        <td class="num">{{ isset($c['score']) ? number_format($c['score'], 4) : '—' }}</td>
                        {{-- inliers = 0 with a decent score means retrieval liked it
                             but geometry never verified it. Usually too few buttons. --}}
                        <td class="num" style="color: {{ ($c['inliers'] ?? 0) === 0 ? 'var(--none)' : 'inherit' }}">
                            {{ $c['inliers'] ?? '—' }}
                        </td>
                        <td>{{ $c['brand'] ?? '—' }}</td>
                        <td>{{ $c['model_code'] ?? '—' }}</td>
                        <td class="num">{{ isset($t['tier1']) ? number_format($t['tier1'], 3) : '—' }}</td>
                        <td class="num">{{ isset($t['geometric']) ? number_format($t['geometric'], 3) : '—' }}</td>
                        <td class="num">{{ $t['brand_agreement'] ?? '—' }}</td>
                        <td class="num">{{ isset($t['aspect_agreement']) ? number_format($t['aspect_agreement'], 3) : '—' }}</td>
                        <td class="num">{{ $t['model_code_bonus'] ?? '—' }}</td>
                        <td>
                            @if ($query->chosen_record_id === ($c['record_id'] ?? null))
                                <span class="tag high">chosen</span>
                            @endif
                        </td>
                    </tr>
                @empty
                    <tr><td colspan="12" class="dim">No candidates returned.</td></tr>
                @endforelse
                </tbody>
            </table>
        </div>
    </div>

    <div class="panel">
        <h2>Raw response</h2>
        <pre>{{ json_encode(['extracted' => $query->extracted, 'candidates' => $candidates], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) }}</pre>
    </div>
@endsection
