import copy
import json
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from test_collect import c, NOW, event, snapshot, supplements, issue

class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def point(self, days, count=10, minutes=0):
        return c.player_record({'response':{'result':1,'player_count':count}},NOW-timedelta(days=days,minutes=minutes))
    def test_recent_90_days_preserved_old_days_summarized(self):
        rows=[self.point(0),self.point(89),self.point(90,20),self.point(90,30,30),self.point(729,40),self.point(730,50)]
        keep,daily=c.retain_observations(self.root,rows,NOW,rows[-1]['observed_at'])
        self.assertEqual(len(keep),2);self.assertEqual(len(daily['days']),2)
        d=daily['days'][-1];self.assertEqual(d['samples'],2);self.assertEqual(d['mean'],25)
        self.assertEqual((d['minimum'],d['maximum']),(20,30))
        self.assertEqual(sum(len(p.read_text().splitlines()) for p in (self.root/'observations').glob('*.jsonl')),2)
    def test_kst_day_boundary(self):
        cutoff=NOW.astimezone(c.KST).date()-timedelta(days=89)
        midnight=c.datetime.combine(cutoff,c.datetime.min.time(),c.KST)
        rows=[c.player_record({'response':{'result':1,'player_count':20}},midnight-timedelta(minutes=30)),
              c.player_record({'response':{'result':1,'player_count':30}},midnight)]
        kept,daily=c.retain_observations(self.root,rows,NOW,rows[0]['observed_at'])
        self.assertEqual(len(kept),1);self.assertEqual(kept[0]['count'],30);self.assertEqual(daily['days'][0]['mean'],20)
    def test_retry_does_not_double_count(self):
        rows=[self.point(90,10),self.point(90,30,30),self.point(0)]
        _,first=c.retain_observations(self.root,rows,NOW,rows[0]['observed_at'])
        _,again=c.retain_observations(self.root,rows,NOW,rows[0]['observed_at'])
        self.assertEqual(first,again)
        # A partial leftover after interruption cannot replace the already finalized whole day.
        _,partial=c.retain_observations(self.root,[rows[0],rows[-1]],NOW,rows[0]['observed_at'])
        self.assertEqual(first,partial)
    def test_invalid_record_does_not_delete_raw(self):
        folder=self.root/'observations';folder.mkdir();p=folder/'2020-01.jsonl';p.write_text('original')
        bad=self.point(90);bad['count']=-1
        with self.assertRaises(ValueError):c.retain_observations(self.root,[bad],NOW,None)
        self.assertEqual(p.read_text(),'original');self.assertFalse((self.root/'data/player-daily.json').exists())
    def test_duplicate_slots_not_double_counted(self):
        row=self.point(90)
        _,daily=c.retain_observations(self.root,[row,row],NOW,row['observed_at'])
        self.assertEqual(daily['days'][0]['samples'],1)
    def test_collection_start_and_heatmap_do_not_reset_after_pruning(self):
        start=c.iso(NOW-timedelta(days=200));recent=[self.point(d) for d in (0,7,14,21,28)]
        result=c.player_feed(recent,NOW,collection_started_at=start)
        self.assertEqual(result['collection_started_at'],start);self.assertTrue(result['heatmap_ready'])
    def test_missing_slots_not_fabricated_by_daily_rollup(self):
        _,daily=c.retain_observations(self.root,[self.point(90)],NOW,None)
        self.assertEqual(daily['days'][0]['samples'],1);self.assertEqual(daily['days'][0]['expected_samples'],48)
    def article(self, url='https://example.org/news/a', age=0):
        return {'url':url,'first_included_at':c.iso(NOW-timedelta(days=age))}
    def test_old_first_seen_prevents_reposted_article_becoming_new(self):
        original=self.article(age=180)
        c.write(self.root/'data/article-registry.json',{'articles':[original]})
        kept,index=c.compact_articles(self.root,[self.article()],{},NOW)
        self.assertEqual(kept,[]);self.assertEqual(index['articles'][original['url']],original['first_included_at'])
        c.write(self.root/'state/article-first-seen.json',index)
        (self.root/'data/article-registry.json').unlink()
        self.assertEqual(c.compact_articles(self.root,[self.article()],{},NOW)[0],[])
    def test_alias_merges_old_identity_after_compaction(self):
        old=self.article('https://example.org/news/old',180)
        c.write(self.root/'state/article-first-seen.json',{'articles':{old['url']:old['first_included_at']}})
        kept,index=c.compact_articles(self.root,[self.article()],{old['url']:'https://example.org/news/a'},NOW)
        self.assertEqual(kept,[]);self.assertEqual(len(index['articles']),1)
    def test_recent_article_remains(self):
        kept,_=c.compact_articles(self.root,[self.article(age=89),self.article('https://example.org/news/b',90)],{},NOW)
        self.assertEqual(len(kept),1)
    def test_long_running_event_survives_raw_source_prune(self):
        doc=c.events_feed([snapshot('2026-01-01',events=[event(start_date='2026-01-01',end_date='2026-12-31')])],supplements(),NOW)
        c.write(self.root/'data/opportunities.json',doc)
        result,state=c.compact_events(self.root,c.events_feed([],supplements(),NOW),{},NOW)
        self.assertEqual(len(result['events']),1);self.assertEqual(result['events'][0]['end_date'],'2026-12-31')
    def test_expired_event_details_removed_but_newer_cancellation_head_kept(self):
        doc=c.events_feed([snapshot('2026-04-01',events=[event(start_date='2026-04-01',end_date='2026-04-02',status='cancelled')])],supplements(),NOW)
        result,state=c.compact_events(self.root,doc,{},NOW)
        self.assertEqual(result['events'],[]);self.assertEqual(state['heads']['event-one']['status'],'cancelled')
        c.write(self.root/'state/event-registry.json',state)
        old=c.events_feed([snapshot('2026-03-01',events=[event(end_date='2026-12-31')])],supplements(),NOW)
        self.assertEqual(c.compact_events(self.root,old,{},NOW)[0]['events'],[])
    def test_newer_event_change_still_applies(self):
        before=c.events_feed([snapshot('2026-10-12',events=[event()])],supplements(),NOW)
        _,state=c.compact_events(self.root,before,{},NOW);c.write(self.root/'state/event-registry.json',state)
        after=c.events_feed([snapshot('2026-10-16',events=[event(status='cancelled')])],supplements(),NOW)
        self.assertEqual(c.compact_events(self.root,after,{},NOW)[0]['events'][0]['status'],'cancelled')
    def test_removed_supplement_not_restored_from_registry(self):
        doc=c.events_feed([],supplements([event(verified_at=c.iso(NOW),evidence='official')]),NOW)
        _,state=c.compact_events(self.root,doc,{},NOW);c.write(self.root/'state/event-registry.json',state)
        result,_=c.compact_events(self.root,c.events_feed([],supplements(),NOW),{},NOW)
        self.assertEqual(result['events'],[])
    def test_compacted_unchanged_source_is_not_downloaded_again(self):
        c.write(self.root/'state/source-state.json',{'sha':'same','issues':{'2026-01-01':'old'}})
        with patch.object(c,'get_json',return_value={'sha':'same','tree':[]}) as get:
            rows,_=c.acquire_snapshots(self.root)
        self.assertEqual(rows,[]);self.assertEqual(get.call_count,1)
    def test_source_cache_prunes_only_old_local_copies(self):
        c.write(self.root/'state/issues/old.json',snapshot('2026-01-01'))
        c.write(self.root/'state/issues/current.json',snapshot('2026-10-16'))
        c.write(self.root/'state/source-state.json',{'issues':{'old':'hash'}})
        c.prune_source_cache(self.root,NOW)
        self.assertFalse((self.root/'state/issues/old.json').exists());self.assertTrue((self.root/'state/issues/current.json').exists())
        self.assertEqual(c.read(self.root/'state/source-state.json')['issues'],{'old':'hash'})
    def test_oversized_output_rejected_before_publish(self):
        doc=c.events_feed([],supplements(),NOW);doc['padding']='x'*(4*1024*1024)
        with self.assertRaises(ValueError):c.validate_feed('opportunities',doc)

if __name__=='__main__':unittest.main()
