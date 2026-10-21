# ちいかわ まんがビューア

[@ngnchiikawa](https://x.com/ngnchiikawa) の投稿を一覧して読む、非公式のファンメイドビューアです。各話から X 上の元投稿へリンクします。

まんがの画像はこのリポジトリには置いていません。表示のたびに X（`pbs.twimg.com`）から読み込みます。作品の権利は権利者に帰属します。

## 更新のしかた

`index.html` は、リポジトリ外のローカル SQLite データベースから再生成し、そのファイルを差し替えて更新します。データベース自体はここには含まれません。再生成に使うスクリプトは参照用に `tools/build_viewer.py` を置いてあります。

```bash
python3 tools/build_viewer.py --db /path/to/chiikawa_manga.db --out index.html
```

生成した `index.html` をコミットして `main` に入れると、GitHub Pages の内容が更新されます。

## GitHub Pages

リポジトリの Settings → Pages で、次のとおり設定します。

- **Deploy from a branch**
- Branch: **main**
- Folder: **/ (root)**

ルートの空ファイル `.nojekyll` により、Jekyll の処理を無効にしています。
