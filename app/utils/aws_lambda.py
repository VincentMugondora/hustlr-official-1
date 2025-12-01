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
        self.use_bedrock_intent = os.getenv('USE_BEDROCK_INTENT', 'false').lower() == 'true'
        self.bedrock_model_id = os.getenv('BEDROCK_MODEL_ID')
        self.bedrock_client = None
        if self.use_bedrock_intent and self.bedrock_model_id:
            self.bedrock_client = boto3.client(
                'bedrock-runtime',
                aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
                region_name=os.getenv('AWS_REGION', 'us-east-1')
            )
    
    async def invoke_question_answerer(self, user_message: str, user_context: Optional[Dict] = None) -> str:
        """
        Invoke Lambda function for AI-powered question answering
        
        Args:
            user_message: The message from the user
            user_context: Optional user context (name, location, booking history)
            
        Returns:
            AI-generated response
        """
        if self.use_bedrock_intent and self.bedrock_client and self.bedrock_model_id:
            return await self._invoke_bedrock(user_message, user_context or {})
        
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

    async def _invoke_bedrock(self, user_message: str, user_context: Optional[Dict[str, Any]] = None) -> str:
        if not self.bedrock_client or not self.bedrock_model_id:
            return "AI service not configured. Please contact support."
        try:
            body = self._build_bedrock_body(user_message, user_context or {})
            response = self.bedrock_client.invoke_model(
                modelId=self.bedrock_model_id,
                body=json.dumps(body).encode("utf-8"),
                contentType="application/json",
                accept="application/json",
            )
            raw_body = response.get("body")
            if hasattr(raw_body, "read"):
                parsed = json.loads(raw_body.read())
            else:
                parsed = json.loads(raw_body)
            content = parsed.get("content")
            if isinstance(content, list):
                texts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        t = item.get("text") or ""
                        if t:
                            texts.append(t)
                if texts:
                    return "\n".join(texts).strip()
            text = parsed.get("output_text") or parsed.get("completion") or ""
            if text:
                return str(text).strip()
            return "I apologize, but I cannot help with that request."
        except ClientError as e:
            print(f"Bedrock invocation error: {e}")
            return "I'm experiencing technical difficulties. Please try again later."
        except Exception as e:
            print(f"Unexpected error in Bedrock service: {e}")
            return "An unexpected error occurred. Please try again."

    def _build_bedrock_body(self, user_message: str, user_context: Dict[str, Any]) -> Dict[str, Any]:
        context_parts = []
        name = user_context.get('name')
        if name:
            context_parts.append(f"User name: {name}")
        location = user_context.get('location')
        if location:
            context_parts.append(f"User location: {location}")
        history = user_context.get('booking_history')
        if history:
            context_parts.append(f"Booking history: {history}")
        context_text = "\n".join(context_parts)
        user_text = f"User message: {user_message}"
        if context_text:
            combined = context_text + "\n" + user_text
        else:
            combined = user_text
        system_prompt = (
            "You are Hustlr, a WhatsApp assistant that helps users find and book local service providers "
            "like plumbers, electricians, and cleaners. Answer in a friendly, concise way suitable for WhatsApp. "
            "If the user is asking to search or book, respond with clear next steps and questions you need."
        )
        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "temperature": 0.4,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": combined,
                        }
                    ],
                }
            ],
        }
