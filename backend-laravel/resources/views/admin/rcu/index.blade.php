@extends('admin.rcu.layout')
@section('title', 'RCU queries')

@section('content')
    <div class="panel">
        <h2>Inspect a photo</h2>
        <p class="dim">
            Runs extraction with the offline build's settings, so what comes back is
            what the catalog would store for this image &mdash; the comparison to
            reach for when a query and its catalog record disagree.
        </p>
        <form method="POST" action="{{ route('admin.rcu.inspect') }}" enctype="multipart/form-data">
            @csrf
            <input type="file" name="photo" accept="image/*" required>
            <button type="submit">Extract</button>
        </form>
        @error('photo')<p style="color: var(--none)">{{ $message }}</p>@enderror
    </div>

    <div class="panel">
        <h2>Queries</h2>
        <p class="filters">
            @foreach (['recent' => 'recent', 'misses' => 'misses', 'low' => 'low / none'] as $key => $label)
                <a href="{{ route('admin.rcu.index', ['filter' => $key]) }}"
                   class="{{ $filter === $key ? 'on' : '' }}">{{ $label }}</a>
            @endforeach
        </p>

        <div class="scroll">
            <table>
                <thead>
                <tr>
                    <th>when</th>
                    <th>request</th>
                    <th>confidence</th>
                    <th>top candidate</th>
                    <th class="num">score</th>
                    <th class="num">inliers</th>
                    <th class="num">buttons</th>
                    <th class="num">ms</th>
                    <th>answer</th>
                </tr>
                </thead>
                <tbody>
                @forelse ($queries as $q)
                    @php $top = ($q->candidates[0] ?? null); @endphp
                    <tr>
                        <td class="dim">{{ $q->created_at?->format('m-d H:i') }}</td>
                        <td><a href="{{ route('admin.rcu.show', $q->request_id) }}">{{ Str::limit($q->request_id, 16, '') }}</a></td>
                        <td><span class="tag {{ $q->confidence }}">{{ $q->confidence }}</span></td>
                        <td>{{ $q->top_record_id ?? '—' }}</td>
                        <td class="num">{{ $q->top_score !== null ? number_format($q->top_score, 3) : '—' }}</td>
                        <td class="num">{{ $top['inliers'] ?? '—' }}</td>
                        <td class="num">{{ $q->extracted['button_count'] ?? '—' }}</td>
                        <td class="num">{{ $q->latency_ms ?? '—' }}</td>
                        <td>
                            @if ($q->none_of_these)
                                <span style="color: var(--none)">none of these</span>
                            @elseif ($q->chosen_record_id)
                                {{-- The pair worth looking at: user disagreed with us. --}}
                                <span style="color: {{ $q->chosen_record_id === $q->top_record_id ? 'var(--high)' : 'var(--medium)' }}">
                                    {{ $q->chosen_record_id }}
                                </span>
                            @else
                                <span class="dim">—</span>
                            @endif
                        </td>
                    </tr>
                @empty
                    <tr><td colspan="9" class="dim">No queries yet.</td></tr>
                @endforelse
                </tbody>
            </table>
        </div>
    </div>
@endsection
