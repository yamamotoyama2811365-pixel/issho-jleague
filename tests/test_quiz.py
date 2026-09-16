import os, tempfile
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
import app

def test_seed_and_quiz_start_submit():
    app.Base.metadata.create_all(app.engine)
    app.init_db()
    client = app.app.test_client()
    r = client.post('/api/quiz/start')
    assert r.status_code == 200
    data = r.get_json()
    assert len(data['questions']) == 10
    answers = {str(q['id']): 'A' for q in data['questions']}
    r2 = client.post('/api/quiz/submit', json={'attempt_id': data['attempt_id'], 'nickname':'TEST','answers':answers})
    assert r2.status_code == 200
    out = r2.get_json()
    assert out['total'] == 10
    assert 0 <= out['score'] <= 10
    assert out['rank'] >= 1
