<?php

namespace Tests\Feature;

use App\Models\RcuQuery;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

/**
 * Feedback capture (plan 6.4). Every answer is a labelled training pair, and
 * "none of these" is the most informative of them -- it says the catalog is
 * missing the remote or the extraction was wrong, which no positive pick
 * ever does. It must therefore be distinguishable from "not answered yet".
 */
class FeedbackTest extends TestCase
{
    use RefreshDatabase;

    private function query(array $overrides = []): RcuQuery
    {
        return RcuQuery::create(array_merge([
            'request_id' => 'abc123def456',
            'upload_path' => 'uploads/x.jpg',
            'confidence' => 'medium',
            'top_record_id' => 'GINZZU_GM-501_0',
            'top_score' => 0.52,
        ], $overrides));
    }

    public function test_it_records_the_chosen_candidate(): void
    {
        $this->query();

        $this->postJson('/api/identify/abc123def456/choose', [
            'record_id' => 'GINZZU_GM-501_1',
        ])->assertOk();

        $q = RcuQuery::sole();
        $this->assertSame('GINZZU_GM-501_1', $q->chosen_record_id);
        $this->assertFalse($q->none_of_these);
        $this->assertNotNull($q->answered_at);
        $this->assertTrue($q->isAnswered());
    }

    public function test_it_records_none_of_these(): void
    {
        $this->query();

        $this->postJson('/api/identify/abc123def456/choose', [
            'none_of_these' => true,
        ])->assertOk();

        $q = RcuQuery::sole();
        $this->assertTrue($q->none_of_these);
        $this->assertNull($q->chosen_record_id);
        $this->assertNotNull($q->answered_at);
    }

    public function test_an_empty_answer_is_rejected(): void
    {
        $this->query();

        // Otherwise this silently marks the query answered with no content,
        // which pollutes the training set with rows that mean nothing.
        $this->postJson('/api/identify/abc123def456/choose', [])
            ->assertStatus(422)
            ->assertJsonPath('error', 'nothing_chosen');

        $this->assertNull(RcuQuery::sole()->answered_at);
    }

    public function test_it_404s_for_an_unknown_request(): void
    {
        $this->postJson('/api/identify/nope/choose', ['record_id' => 'x'])
            ->assertStatus(404);
    }

    public function test_the_misses_scope_finds_disagreements_and_none_of_these(): void
    {
        // agreed with us -- not a miss
        $this->query(['request_id' => 'agree', 'chosen_record_id' => 'GINZZU_GM-501_0',
                      'answered_at' => now()]);
        // disagreed -- a miss
        $this->query(['request_id' => 'disagree', 'chosen_record_id' => 'OTHER_0',
                      'answered_at' => now()]);
        // none of these -- a miss
        $this->query(['request_id' => 'none', 'none_of_these' => true,
                      'answered_at' => now()]);
        // unanswered -- not a miss, and must not be counted as one
        $this->query(['request_id' => 'pending']);

        $misses = RcuQuery::misses()->pluck('request_id')->all();

        sort($misses);
        $this->assertSame(['disagree', 'none'], $misses);
    }
}
