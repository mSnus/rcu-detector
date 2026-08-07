<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

/**
 * A record_id comes from a filename, and filenames are case-sensitive.
 *
 * MySQL's default collation is not. `Irbis_0` and `irbis_0` are two different
 * photographs of two different remotes on disk, and two different documents in
 * the token index -- but one key to a `utf8mb4_..._ci` unique index. The second
 * import overwrote the first, reported "0 failed", and left the catalogue two
 * rows short of the fingerprint directory with no error anywhere.
 *
 * The full-catalogue rebuild made it visible: 13584 fingerprint files, 13582
 * rows. It would have been just as wrong before, on a smaller scale, and the
 * only reason it was caught is that resync-catalog.sh compares the two counts
 * and refuses to continue when they disagree. A record in the index whose id
 * resolves to *another record's* row is worse than one that resolves to
 * nothing: the match succeeds and returns the wrong remote's metadata.
 *
 * `utf8mb4_bin` rather than `_as_cs`, because this column is an identifier
 * rather than text: exact bytes, no case folding, no accent folding. The
 * referencing columns in rcu_queries get the same treatment so comparisons
 * between them stay well-defined.
 */
return new class extends Migration
{
    private const COLUMNS = [
        ['rcu_fingerprints', 'record_id', 191, false],
        ['rcu_queries', 'top_record_id', 191, true],
        ['rcu_queries', 'chosen_record_id', 191, true],
    ];

    public function up(): void
    {
        if (DB::getDriverName() !== 'mysql') {
            // sqlite (the test suite) compares strings case-sensitively
            // already, so there is nothing to change and nothing to assert.
            return;
        }

        foreach (self::COLUMNS as [$table, $column, $length, $nullable]) {
            if (! Schema::hasTable($table) || ! Schema::hasColumn($table, $column)) {
                continue;
            }
            $null = $nullable ? 'NULL' : 'NOT NULL';
            DB::statement("ALTER TABLE `{$table}` MODIFY `{$column}` "
                . "VARCHAR({$length}) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin {$null}");
        }
    }

    public function down(): void
    {
        if (DB::getDriverName() !== 'mysql') {
            return;
        }

        foreach (self::COLUMNS as [$table, $column, $length, $nullable]) {
            if (! Schema::hasTable($table) || ! Schema::hasColumn($table, $column)) {
                continue;
            }
            $null = $nullable ? 'NULL' : 'NOT NULL';
            DB::statement("ALTER TABLE `{$table}` MODIFY `{$column}` "
                . "VARCHAR({$length}) {$null}");
        }
    }
};
