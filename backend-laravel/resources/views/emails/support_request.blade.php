<p>Запрос на подбор пульта с сайта.</p>

<table cellpadding="4">
    <tr><td><strong>Имя:</strong></td><td>{{ $req->name }}</td></tr>
    <tr><td><strong>Телефон:</strong></td><td>{{ $req->phone }}</td></tr>
    <tr><td><strong>Получен:</strong></td><td>{{ $req->created_at }}</td></tr>
</table>

@if ($req->top_record_id)
    <p><strong>Автоматика предложила:</strong>
        {{ $req->top_title ?: $req->top_record_id }}
        (уверенность: {{ $req->confidence }})</p>
@else
    <p><strong>Автоматика совпадений не нашла.</strong></p>
@endif

<p>Фотография приложена к письму.</p>

<p style="color:#666;font-size:12px">
    Заявка №{{ $req->id }}@if ($req->request_id), запрос {{ $req->request_id }}@endif
</p>
