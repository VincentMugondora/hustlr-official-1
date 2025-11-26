import os
import boto3
import json
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError

class AWSLambdaService:
    """Service for interacting with AWS Lambda functions"""
    
    def __init__(self):
        self.lambda_client = boto3.client(
            'lambda',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION', 'us-east-1')
        )
        self.question_answerer_function = os.getenv('AWS_LAMBDA_QUESTION_ANSWERER_FUNCTION_NAME')
    
    async def invoke_question_answerer(self, user_message: str, user_context: Optional[Dict] = None) -> str:
        """
        Invoke Lambda function for AI-powered question answering
        
        Args:
            user_message: The message from the user
            user_context: Optional user context (name, location, booking history)
            
        Returns:
            AI-generated response
        """
        if not self.question_answerer_function:
            return "AI service not configured. Please contact support."
        
        try:
            payload = {
                'user_message': user_message,
                'user_context': user_context or {}
            }
            
            response = self.lambda_client.invoke(
                FunctionName=self.question_answerer_function,
                InvocationType='RequestResponse',
                Payload=json.dumps(payload)
            )
            
            result = json.loads(response['Payload'].read())
            
            if 'errorMessage' in result:
                return f"Sorry, I'm having trouble understanding. Could you try rephrasing?"
            
            return result.get('response', 'I apologize, but I cannot help with that request.')
            
        except ClientError as e:
            print(f"Lambda invocation error: {e}")
            return "I'm experiencing technical difficulties. Please try again later."
        except Exception as e:
            print(f"Unexpected error in Lambda service: {e}")
            return "An unexpected error occurred. Please try again."
