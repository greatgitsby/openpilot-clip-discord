pipeline {
    agent any
    environment {
        APP_NAME = 'openpilot-clip-discord'
        DISCORD_TOKEN = credentials('DISCORD_TOKEN')
    }
    stages {
        stage('Init') {
            steps {
                sh 'bash init.sh'
            }
        }
        stage('Deploy') {
            steps {
                sh "pkill -f 'uv run main.py' || true"
                sh "sleep 2"
                sh "BUILD_ID=dontKillMe nohup bash live.sh > /dev/null 2>&1 &"
            }
        }
    }
    post {
        failure {
            echo 'Build failed!'
        }
        success {
            echo 'Deployed successfully'
        }
    }
}
