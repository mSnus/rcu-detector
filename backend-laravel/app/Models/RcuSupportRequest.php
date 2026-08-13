<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

class RcuSupportRequest extends Model
{
    protected $table = 'rcu_support_requests';

    protected $fillable = [
        'request_id', 'name', 'phone', 'image_path',
        'confidence', 'top_record_id', 'top_title',
        'emailed_at', 'forwarded_at', 'delivery_error',
    ];

    protected $casts = [
        'emailed_at' => 'datetime',
        'forwarded_at' => 'datetime',
    ];
}
