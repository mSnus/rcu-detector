@extends('admin.rcu.layout')
@section('title', 'Record ' . $record->record_id)

@section('content')
    @if (session('status'))
        <div class="panel" style="border-color: var(--high)">{{ session('status') }}</div>
    @endif

    <div class="panel">
        <h2>{{ $record->record_id }}</h2>
        @if ($record->title)
            {{-- The catalogue's own name, verbatim. Not parsed into brand and
                 model: brand_text/model_text hold what OCR read off the image,
                 and keeping them apart is what lets the two be compared. --}}
            <p style="font-size: 15px">{{ $record->title }}</p>
        @endif
        <p class="dim">
            from {{ $record->source_image }} (crop {{ $record->crop_index }})
            &middot; built {{ $record->built_at?->format('Y-m-d H:i') }}
            @if ($record->itemUrl())
                &middot; <a href="{{ $record->itemUrl() }}" target="_blank" rel="noopener">
                    item {{ $record->model_id }} on the catalogue &nearr;</a>
            @endif
            &middot; <a href="{{ route('admin.rcu.catalog') }}">back to catalog</a>
        </p>

        <form method="POST" action="{{ route('admin.rcu.review', $record->record_id) }}">
            @csrf
            @if ($record->reviewed)
                <input type="hidden" name="unreview" value="1">
                <button type="submit" style="background: #6e7681">Return to review queue</button>
            @else
                <button type="submit">Mark reviewed</button>
            @endif
        </form>
    </div>

    <div class="row">
        <div class="panel">
            <h2>Extraction</h2>
            <table>
                <tr><th>quality</th><td class="num">{{ number_format($record->quality_score, 3) }}</td></tr>
                <tr><th>buttons</th><td class="num">{{ $record->button_count }}</td></tr>
                <tr><th>aspect</th><td class="num">{{ number_format($record->aspect_ratio, 3) }}</td></tr>
                <tr><th>brand</th><td>{{ $record->brand_text ?? '—' }}</td></tr>
                <tr><th>model code</th><td>{{ $record->model_text ?? '—' }}</td></tr>
                <tr>
                    <th>orientation</th>
                    <td>
                        {{ $record->orientation_flipped ? 'flipped' : 'upright' }}
                        (conf {{ number_format($record->orientation_conf, 2) }})
                        @if ($record->orientation_conf < 0.5)
                            <br><span style="color: var(--medium)">
                                Unresolved &mdash; indexed both ways up. Matches against
                                this record carry that ambiguity.
                            </span>
                        @endif
                    </td>
                </tr>
                <tr><th>model_id</th><td>{{ $record->model_id ?? '— not linked to a catalog model —' }}</td></tr>
            </table>
        </div>

        <div class="panel">
            <h2>Rectified crop</h2>
            @if ($hasCrop)
                <img class="shot" src="{{ route('admin.rcu.crop', $record->record_id) }}"
                     alt="rectified crop for {{ $record->record_id }}">
            @else
                <p class="dim">No crop on disk. Expected {{ $record->record_id }}.jpg
                   under the configured norm directory.</p>
            @endif
        </div>
    </div>

    <div class="panel">
        <h2>Build overlay</h2>
        <p class="dim">
            What the extractor saw. Look here first &mdash; essentially every bug
            in this project was found in an overlay rather than by reasoning
            about the code.
        </p>
        @if ($hasOverlay)
            <img class="shot" src="{{ route('admin.rcu.build-overlay', $record->record_id) }}"
                 alt="build overlay for {{ $record->record_id }}">
        @else
            <p class="dim">No overlay on disk for this record.</p>
        @endif
    </div>

    <div class="panel">
        <h2>Fingerprint</h2>
        <pre>{{ json_encode($record->fingerprint, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) }}</pre>
    </div>
@endsection
