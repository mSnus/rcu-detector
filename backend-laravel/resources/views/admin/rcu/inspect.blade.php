@extends('admin.rcu.layout')
@section('title', 'Extraction')

@section('content')
    <p><a href="{{ route('admin.rcu.index') }}">&larr; all queries</a></p>

    @if ($error)
        <div class="panel">
            <h2>Extraction failed</h2>
            <p style="color: var(--none)">{{ $error }}</p>
        </div>
    @else
        <div class="panel">
            <h2>Bodies found: {{ $result['bodies_found'] ?? 0 }}</h2>
            <p class="dim">
                Extracted with the offline build's settings (ensemble on, full OCR),
                not the query path's. More than one body is normal &mdash; two remotes
                side by side, or a remote and the same remote in its packaging.
            </p>
        </div>

        @foreach ($result['fingerprints'] ?? [] as $i => $fp)
            @php $stats = $fp['stats'] ?? []; @endphp
            <div class="panel">
                <h2>Body {{ $i }}</h2>
                <table>
                    <tr><th>brand</th><td>{{ $fp['brand'] ?? '—' }}
                        <span class="dim">{{ $fp['brand_source'] ?? '' }}</span></td></tr>
                    <tr><th>model code</th><td>{{ $fp['model_code'] ?? '—' }}</td></tr>
                    <tr><th>buttons</th><td>{{ $stats['n_buttons'] ?? '—' }}</td></tr>
                    <tr><th>label recall</th><td>{{ $stats['label_recall'] ?? '—' }}</td></tr>
                    <tr><th>aspect</th><td>{{ $fp['body']['aspect'] ?? '—' }}</td></tr>
                    <tr><th>quality</th><td>{{ $fp['extract_quality'] ?? '—' }}</td></tr>
                    <tr>
                        <th>orientation</th>
                        <td>
                            {{ ($stats['orientation_flipped'] ?? false) ? 'flipped' : 'upright' }}
                            <span class="dim">conf {{ $stats['orientation_conf'] ?? '—' }}</span>
                        </td>
                    </tr>
                </table>
                <pre>{{ json_encode($fp, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) }}</pre>
            </div>
        @endforeach
    @endif
@endsection
