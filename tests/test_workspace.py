import subprocess
import tempfile
import unittest
from pathlib import Path

from mcp_aek.workspace import list_files, inspect_file, save_text, git, file_action, artifacts
from mcp_aek.sessions import SessionStore


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'readme.txt').write_text('before\n')

    def tearDown(self):
        self.temp.cleanup()

    def test_file_boundary_and_conflict(self):
        self.assertEqual(list_files(self.root)[0]['name'], 'readme.txt')
        outside = self.root.parent / 'outside-aek-test'
        (self.root / 'link').symlink_to(outside)
        for path in ('../outside', 'link/file', '.git/config', '.aek/sessions.sqlite3'):
            with self.assertRaises(ValueError): inspect_file(self.root, path)
        info = inspect_file(self.root, 'readme.txt')
        diff = save_text(self.root, 'readme.txt', 'after\n', info['sha256'], preview=True)['diff']
        self.assertIn('+after', diff)
        self.assertEqual((self.root / 'readme.txt').read_text(), 'before\n')
        save_text(self.root, 'readme.txt', 'after\n', info['sha256'])
        with self.assertRaises(ValueError): save_text(self.root, 'readme.txt', 'overwrite', info['sha256'])

    def test_real_git_status_diff_stage_and_commit(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=self.root, check=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.invalid'], cwd=self.root, check=True)
        self.assertIn('readme.txt', git(self.root, 'status')['output'])
        self.assertTrue(git(self.root, 'stage', 'readme.txt')['ok'])
        self.assertIn('+before', git(self.root, 'staged')['output'])
        self.assertTrue(git(self.root, 'unstage', 'readme.txt')['ok'])
        self.assertTrue(git(self.root, 'stage', 'readme.txt')['ok'])
        self.assertTrue(git(self.root, 'commit', 'first commit')['ok'])
        self.assertIn('first commit', git(self.root, 'log')['output'])
        (self.root / 'readme.txt').unlink()
        self.assertTrue(git(self.root, 'stage', 'readme.txt')['ok'])
        self.assertIn('-before', git(self.root, 'staged')['output'])
        self.assertTrue(git(self.root, 'unstage', 'readme.txt')['ok'])
        self.assertIn(' D readme.txt', git(self.root, 'status')['output'])
        subprocess.run(['git','restore','readme.txt'],cwd=self.root,check=True)
        original = subprocess.check_output(['git', 'branch', '--show-current'], cwd=self.root, text=True).strip()
        self.assertTrue(git(self.root, 'branch', 'feature/test')['ok'])
        self.assertTrue(git(self.root, 'switch', original)['ok'])

    def test_session_metadata_is_locally_excluded_from_git(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','config','user.name','Test'],cwd=self.root,check=True)
        subprocess.run(['git','config','user.email','test@example.invalid'],cwd=self.root,check=True)
        subprocess.run(['git','add','readme.txt'],cwd=self.root,check=True)
        subprocess.run(['git','commit','-q','-m','baseline'],cwd=self.root,check=True)
        self.assertFalse((self.root/'.gitignore').exists())
        SessionStore(self.root).current()
        self.assertEqual(subprocess.check_output(['git','status','--porcelain'],cwd=self.root,text=True),'')
        self.assertFalse((self.root/'.gitignore').exists())
        self.assertIn('/.aek/',(self.root/'.git/info/exclude').read_text())

    def test_file_actions_and_artifact_boundary(self):
        file_action(self.root, 'new-folder', '', 'src')
        file_action(self.root, 'new-file', 'src', 'build.log')
        self.assertEqual(artifacts(self.root)[0]['path'], 'src/build.log')
        file_action(self.root, 'rename', 'src/build.log', 'renamed.log')
        with self.assertRaises(ValueError):file_action(self.root, 'delete', 'src')
        file_action(self.root, 'delete', 'src/renamed.log')
        file_action(self.root, 'delete', 'src')
        with self.assertRaises(ValueError):file_action(self.root, 'new-file', '', '../escape')
