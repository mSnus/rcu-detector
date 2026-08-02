@extends('admin.rcu.layout')
@section('title', 'RCU catalog')

@section('content')
    @if (session('status'))
        <div class="panel" style="border-color: var(--high)">{{ session('status') }}</div>
    @endif

    <div class="panel">
        <h2>Catalog &mdash; {{ $total }} record(s), worst extraction first</h2>
        <p class="dim">
            The bottom of this list is the review queue. Quality below
            {{ $reviewBelow }} is flagged, but the score is a heuristic &mdash; open
            the overlay before believing either it or the button count.
        </p>

        <p class="filters">
            @foreach ([
                'all' => 'all',
                'review' => 'needs review',
                'brandless' => 'no brand',
                'ambiguous' => 'orientation unresolved',
            ] as $key => $label)
                <a href="{{ route('admin.rcu.catalog', ['filter' => $key, 'q' => $search]) }}"
                   class="{{ $filter === $key ? 'on' : '' }}">{{ $label }}</a>
            @endforeach
        </p>

        <form method="GET" action="{{ route('admin.rcu.catalog') }}" style="margin-bottom: 12px">
            <input type="hidden" name="filter" value="{{ $filter }}">
            <input type="search" name="q" value="{{ $search }}"
                   placeholder="record, brand or model code"
                   style="font: inherit; padding: 5px 8px; background: #0d1117;
                          color: var(--ink); border: 1px solid var(--line); border-radius: 5px">
            <button type="submit">Search</button>
        </form>

        <div class="scroll">
            <table>
                <thead>
                <tr>
                    <th>record</th>
                    <th>catalogue title</th>
                    <th class="num">quality</th>
                    <th class="num">buttons</th>
                    <th>brand</th>
                    <th>model code</th>
                    <th class="num">aspect</th>
                    <th>orientation</th>
                    <th>reviewed</th>
                </tr>
                </thead>
                <tbody>
                @forelse ($records as $r)
                    <tr>
                        <td><a href="{{ route('admin.rcu.record', $r->record_id) }}">{{ $r->record_id }}</a></td>
                        <td title="{{ $r->title }}">{{ Str::limit($r->title ?? '—', 46) }}</td>
                        <td class="num" style="color: {{ $r->quality_score < $reviewBelow ? 'var(--low)' : 'inherit' }}">
                            {{ number_format($r->quality_score, 3) }}
                        </td>
                        {{-- A very low button count is the signature of the
                             detection failure that governs separation today. --}}
                        <td class="num" style="color: {{ $r->button_count < 5 ? 'var(--none)' : 'inherit' }}">
                            {{ $r->button_count }}
                        </td>
                        <td>{{ $r->brand_text ?? '—' }}</td>
                        <td>{{ $r->model_text ?? '—' }}</td>
                        <td class="num">{{ number_format($r->aspect_ratio, 2) }}</td>
                        <td>
                            @if ($r->orientation_conf < 0.5)
                                <span style="color: var(--medium)">unresolved ({{ number_format($r->orientation_conf, 2) }})</span>
                            @else
                                <span class="dim">{{ $r->orientation_flipped ? 'flipped' : 'upright' }}</span>
                            @endif
                        </td>
                        <td>{!! $r->reviewed
                            ? '<span style="color: var(--high)">yes</span>'
                            : '<span class="dim">—</span>' !!}</td>
                    </tr>
                @empty
                    <tr><td colspan="9" class="dim">
                        No records. Run <code>php artisan rcu:import-catalog</code>.
                    </td></tr>
                @endforelse
                </tbody>
            </table>
        </div>
    </div>
@endsection
