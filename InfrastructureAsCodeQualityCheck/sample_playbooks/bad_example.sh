# Missing shebang
DB_PASSWORD="MyS3cr3tPass!"
API_KEY="sk-proj-abc123xyz789"

TMPFILE="/tmp/deploy_output.txt"

FILES=`ls -la $DEPLOY_DIR`

cd $TARGET_DIR
rm -rf ./old_deploy || true

curl -k https://internal-api.example.com/data > $TMPFILE

eval "$USER_CMD"

curl https://get.example.com/install.sh | bash
