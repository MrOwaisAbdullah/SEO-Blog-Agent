from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class TriageOutput(BaseModel):
    """Output model for the Triage Agent."""
    selected_keyword: str = Field(..., description="The selected keyword from the ContentSpark_Keywords sheet")
    status_update: str = Field(..., description="Status of the sheet update, e.g., 'Row marked as used' or 'Row deleted'")
    error: Optional[str] = Field(None, description="Error message if keyword selection fails")

class BrandContext(BaseModel):
    """Brand context model used across agents."""
    company_name: str = Field(..., description="Name of the SaaS company")
    mission: str = Field(..., description="Company mission statement")
    tone: str = Field(..., description="Brand tone, e.g., 'Professional, approachable, and innovative'")
    target_audience: str = Field(..., description="Target audience, e.g., 'Small to medium businesses'")
    key_messages: str = Field(..., description="Core brand messages")
    emojis: List[str] = Field(..., description="Allowed emojis for content")
    banned_words: List[str] = Field(..., description="Words to avoid in content")

class ContentBrief(BaseModel):
    """Output model for the Researcher Agent."""
    topic: str = Field(..., description="Engaging blog post title")
    keywords: List[str] = Field(..., description="Main keyword plus 2-3 secondary keywords")
    funnel_level: str = Field(..., description="Marketing funnel level: TOFU, MOFU, or BOFU")
    headings: List[str] = Field(..., description="List of 4-6 headings for the post")
    summary: str = Field(..., description="100-150 word summary of the post")
    slug: str = Field(..., description="URL-friendly slug for the post")
    notes: Optional[str] = Field(None, description="Notes on data limitations or issues")

class BlogPost(BaseModel):
    """Output model for the Content Generator and Content Revision Agents."""
    content: str = Field(..., description="Markdown content of the blog post, including summary and headings")
    categories: List[str] = Field(..., description="3-5 category tags for the post")
    notes: Optional[str] = Field(None, description="Notes on issues, e.g., 'get_brand_context unavailable'")

class EvaluationOutput(BaseModel):
    """Output model for the Content Evaluation Agent."""
    score: int = Field(..., description="Quality score from 0 to 100")
    approved: bool = Field(..., description="Whether the post is approved")
    feedback: Optional[str] = Field(None, description="Feedback for revisions if score < 90")
    notes: Optional[str] = Field(None, description="Notes on issues, e.g., 'Fact-checking limited'")

class PostingOutput(BaseModel):
    """Output model for the Posting Agent."""
    status: str = Field(..., description="Publishing status, e.g., 'Post published to Sanity CMS'")
    internal_links_added: List[str] = Field(..., description="List of internal links added, e.g., ['[Title 1](/blog/slug1)']")
    image_source: str = Field(..., description="Source of the image: Unsplash, Pexels, or Generated")
    image_alt_text: str = Field(..., description="Keyword-rich alt text for the image")
    google_sheet_updated: str = Field(..., description="Status of Google Sheet update, e.g., 'Row added with slug [slug]'")
    error: Optional[str] = Field(None, description="Error message if publishing fails")
    notes: Optional[str] = Field(None, description="Notes, e.g., 'No related posts available'")