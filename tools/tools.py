from agents import function_tool
import os
import requests
import json
from slugify import slugify
import textstat
from language_tool_python import LanguageTool
from huggingface_hub import InferenceClient
import time
from PIL import Image
import io
from lib.sanity_adapter import SanityAdapter
from dotenv import load_dotenv
import tempfile
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

load_dotenv()

logger = logging.getLogger(__name__)

# Define Pydantic model for FAQ items
class FAQItem(BaseModel):
    question: str = Field(..., description="The FAQ question")
    answer: str = Field(..., description="The FAQ answer")

# ContentSpark AI – Brand Context Engine
@function_tool
def get_brand_context_tool():
    """
    Returns the single source of truth for all ContentSpark AI messaging.
    Use this object to:
      • Feed GPT / Gemini prompts
      • Generate landing-page copy
      • Create social captions, ads, emails, changelogs, help-docs
      • QA every asset before it ships
    """
    return {
        # Core Identity
        "company_name": "ContentSpark AI",
        "tagline": "Your brand voice, on autopilot.",
        "mission": (
            "We help creators, agencies, and SMBs create scroll-stopping, "
            "on-brand social content in minutes—then remember every nuance "
            "so their voice stays unmistakably theirs, forever."
        ),

        # Brand Personality
        "tone": (
            "Creative, upbeat, and supportive—like a friendly co-pilot who’s "
            "fluent in memes AND metrics. We celebrate small wins, demystify AI, "
            "and never gate-keep a good growth hack."
        ),
        "voice_examples": {
            "good": [
                "Your next caption is 3 clicks away—let’s spark it ✨",
                "We just auto-planned your week of posts. Go grab a coffee ☕️"
            ],
            "avoid": [
                "Our paradigm-shifting technology revolutionizes the space."
            ]
        },

        # Visual & Emoji Language
        "primary_emojis": ["✨", "📈", "🎯", "🧠", "🚀"],
        "accent_emojis": ["☕️", "😎", "🔥", "🤝"],
        "color_palette": {
            "primary": "#3B82F6",  # Spark Blue
            "accent": "#10B981",   # Growth Green
            "neutral": "#F8FAFC",  # Cloud White
            "warning": "#F59E0B"   # Warm Amber
        },

        # Audience Personas
        "personas": {
            "solo_creator": {
                "name": "Solo Creator",
                "pain": "No time, inconsistent voice, stuck in Canva hell",
                "gain": "One hub to ideate, write, schedule, and grow",
                "trigger": "Hit 1 000 followers → need daily posts"
            },
            "micro_agency": {
                "name": "Micro-Agency (2-10 clients)",
                "pain": "Juggling 5 Google Docs, 3 tones, 2 interns",
                "gain": "Multi-profile memory + approval flows = scale",
                "trigger": "Client #3 threatens to churn"
            },
            "smb_owner": {
                "name": "SMB Owner",
                "pain": "Marketing on nights/weekends, zero design skills",
                "gain": "AI does 80 %, they tweak 20 %, results in 5 min",
                "trigger": "Quarterly sales dip"
            },
        },

        # Messaging Pillars
        "key_messages": [
            "Brand consistency without brain drain.",
            "From blank page to scheduled post in under 3 minutes.",
            "Your brand voice—saved, searchable, and AI-ready.",
            "Scale social without sounding like everyone else."
        ],

        # Differentiators → Proof Points
        "proof_points": {
            "memory": (
                "Our vector memory recalls every caption you’ve ever posted, "
                "keeping tone and emojis locked in—even across 50 clients."
            ),
            "speed": (
                "Average user goes from prompt to scheduled post in 2 min 37 s "
                "(measured via PostHog)."
            ),
            "price": (
                "Up to 83 % cheaper than Hootsuite for 10 profiles."
            )
        },
        "preferred_terms": {
            "AI": "your creative sidekick",
            "users": "creators & teams",
            "analytics": "insights that matter",
            "scheduling": "calendar on autoplay"
        },
        
        # Language Rules
        "banned_words": ["enterprise", "solution", "synergy", "leverage (as verb)", "disruptive", "cutting-edge", "game-changer", "revolutionary", "revolutionize", "dive in", "venture", "innovative", "realm", "adhere", "delve", "reimagine", "robust", "orchestrate", "diverse", "commendable", "embrace", "paramount", "beacon", "captivate", "commendable", "advancement in the realm", "aims to bridge", "aims to democratize", "aims to foster innovation and collaboration", "becomes increasingly evident", "behind the veil", "breaking barriers", "breakthrough has the potential to revolutionize the way", "bringing us", "bringing us closer to a future", "by combining the capabilities", "by harnessing the power", "capturing the attention", "continue to advance", "continue to make significant strides", "continue to push the boundaries", "continues to progress rapidly", "crucial to be mindful", "crucially", "cutting-edge", "drive the next big", "encompasses a wide range of real-life scenarios", "enhancement further enhances", "ensures that even", "essential to understand the nuances", "excitement", "exciting opportunities", "exciting possibilities", "exciting times lie ahead as we unlock the potential of", "excitingly", "expanded its capabilities", "expect to witness transformative breakthroughs", "expect to witness transformative breakthroughs in their capabilities", "exploration of various potential answers", "explore the fascinating world", "exploring new frontiers", "exploring this avenue", "foster the development", "future might see us placing", "groundbreaking way", "groundbreaking advancement", "groundbreaking study", "groundbreaking technology", "have come a long way in recent years", "hold promise", "implications are profound", "improved efficiency in countless ways", "in conclusion", "in the fast-paced world", "innovative service", "intrinsic differences", "it discovered an intriguing approach", "it remains to be seen", "it serves as a stepping stone towards the realization", "latest breakthrough signifies", "latest offering", "let’s delve into the exciting details", "main message to take away", "make informed decisions", "mark a significant step forward", "mind-boggling figure","more robust evaluation","for instance","navigate the landscape","notably","one step closer","one thing is clear","only time will tell","opens up exciting possibilities","paving the way for enhanced performance","possibilities are endless","potentially revolutionizing the way","push the boundaries","raise fairness concerns","raise intriguing questions","rapid pace of development","rapidly developing","redefine the future","remarkable abilities","remarkable breakthrough","remarkable proficiency","remarkable success","remarkable tool","remarkably","elevate ","captivate ","tapestry ","delve ","leverage ","resonate ","foster ","endeavor ","embark ","unleash ","renowned","represent a major milestone","represents a significant milestone in the field","revolutionize the way","revolutionizing the way","risks of drawing unsupported conclusions","seeking trustworthiness","significant step forward","significant strides","the necessity of clear understanding","there is still room for improvement","transformative power","truly exciting","uncover hidden trends","understanding of the capabilities","unleashing the potential","unlocking the power","unraveling","we can improve understanding and decision-making","welcome your thoughts","what sets this apart","what’s more","with the introduction","bespoke","whimsical","meticulous","emerge","refrain","vibrant","reimagine","evolve","supercharge","pivotal"],

        # Call-to-Actions (CTA Library)
        "ctas": {
            "waitlist": "Save my spot + import my posts",
            "trial": "Start free, no card",
            "upgrade": "Unlock unlimited profiles",
            "share": "Show off my Brand DNA file"
        },

        # Social Caption Templates
        "caption_templates": [
            "✨ New week, new posts—crafted in 3 minutes flat. Who else is letting AI handle the grind? #ContentSpark",
            "📈 When your AI remembers every emoji you’ve ever used… consistency level: expert.",
            "☕️ Just batch-created 30 days of content before my latte cooled. Game on.",
            "🧠 Your brand voice deserves a memory. Export & share your .brand file today!"
        ],

        # Support & Help Tone
        "support_tone": (
            "We’re in your DMs with GIFs, step-by-step Loom videos, "
            "and zero corporate fluff—because your growth > our inbox zero."
        ),

        # Compliance & Trust
        "data_message": (
            "Your captions stay yours. 256-bit AES encryption, GDPR/CCPA ready, "
            "and we’ll delete everything with one click."
        )
    }

# Owais Abdullah – Personal Brand Context
@function_tool
def get_author_context_tool():
    """
    Returns the single source of truth for all Owais Abdullah.
    Use this object to:
      • Guide GPT/Gemini prompts
      • Write website copy, captions, ads, and outreach emails
      • Keep a consistent tone across all platforms
    """
    return {
        # Core Identity
        "brand_name": "Owais Abdullah",
        "tagline": "Web, AI & Automation—Made Simple.",
        "mission": (
            "Helping businesses and creators build smarter web experiences, "
            "AI-driven tools, and automation systems—without overcomplicating technology."
        ),

        # Brand Personality
        "tone": (
            "Approachable, clear, and solution-focused—like a tech-savvy friend who "
            "breaks complex ideas into simple, actionable steps. Confident but never arrogant."
        ),
        "voice_examples": {
            "good": [
                "Smart tools don’t need to feel complicated—let’s make them work for you.",
                "From your idea to a live, polished product—handled with care and precision.",
                "Your project deserves more than templates—it deserves thoughtful development."
            ],
            "avoid": [
                "Our paradigm-shifting solution will revolutionize the digital landscape.",
                "This disruptive technology will change everything overnight."
            ]
        },

        # Visual & Emoji Language
        "primary_emojis": ["🚀", "🤖", "⚙️", "🌐", "💡"],
        "accent_emojis": ["📈", "🛠️", "✅", "☕"],
        "color_palette": {
            "primary": "#3A69FF",   # Your primary accent
            "accent": "#1E293B",    # Dark slate for contrast
            "neutral": "#F8FAFC",   # Soft white
            "highlight": "#10B981"  # Secondary pop of green
        },

        # Audience Personas
        "personas": {
            "startup_founder": {
                "name": "Startup Founder",
                "pain": "Limited resources, need reliable web or AI tools quickly.",
                "gain": "A partner who can design, build, and ship efficiently.",
                "trigger": "Looking to launch an MVP or improve workflows."
            },
            "smb_owner": {
                "name": "Small Business Owner",
                "pain": "Wants an online presence or automation without technical headaches.",
                "gain": "A clear plan and smooth delivery of their site or app.",
                "trigger": "Needs to boost sales or streamline operations."
            },
            "creator": {
                "name": "Content Creator",
                "pain": "Struggles with scaling content and repurposing efficiently.",
                "gain": "AI tools that handle research, posting, and automation.",
                "trigger": "Ready to grow across multiple platforms."
            },
        },

        # Messaging Pillars
        "key_messages": [
            "Web and AI development without unnecessary complexity.",
            "Your ideas, built into reliable apps, sites, or agents.",
            "Automation and AI that save time and boost results.",
            "Partnership over jargon—clear, honest communication at every step."
        ],

        # Differentiators → Proof Points
        "proof_points": {
            "experience": "2+ years delivering professional web apps, AI tools, and automation workflows.",
            "breadth": "Full-stack expertise: React, Next.js, TypeScript, Tailwind CSS, Python, Sanity, WordPress, and AI agents.",
            "track_record": "Projects include e-commerce marketplaces, SEO blog agents, AI social tools, and renting platforms."
        },

        # Preferred and Banned Terms
        "preferred_terms": {
            "AI": "smart automation",
            "users": "clients or creators",
            "website": "web experience",
            "tool": "solution"
        },

        "banned_words": ["enterprise", "solution", "synergy", "leverage (as verb)", "disruptive", "cutting-edge", "game-changer", "revolutionary", "revolutionize", "dive in", "venture", "innovative", "realm", "adhere", "delve", "reimagine", "robust", "orchestrate", "diverse", "commendable", "embrace", "paramount", "beacon", "captivate", "commendable", "advancement in the realm", "aims to bridge", "aims to democratize", "aims to foster innovation and collaboration", "becomes increasingly evident", "behind the veil", "breaking barriers", "breakthrough has the potential to revolutionize the way", "bringing us", "bringing us closer to a future", "by combining the capabilities", "by harnessing the power", "capturing the attention", "continue to advance", "continue to make significant strides", "continue to push the boundaries", "continues to progress rapidly", "crucial to be mindful", "crucially", "cutting-edge", "drive the next big", "encompasses a wide range of real-life scenarios", "enhancement further enhances", "ensures that even", "essential to understand the nuances", "excitement", "exciting opportunities", "exciting possibilities", "exciting times lie ahead as we unlock the potential of", "excitingly", "expanded its capabilities", "expect to witness transformative breakthroughs", "expect to witness transformative breakthroughs in their capabilities", "exploration of various potential answers", "explore the fascinating world", "exploring new frontiers", "exploring this avenue", "foster the development", "future might see us placing", "groundbreaking way", "groundbreaking advancement", "groundbreaking study", "groundbreaking technology", "have come a long way in recent years", "hold promise", "implications are profound", "improved efficiency in countless ways", "in conclusion", "in the fast-paced world", "innovative service", "intrinsic differences", "it discovered an intriguing approach", "it remains to be seen", "it serves as a stepping stone towards the realization", "latest breakthrough signifies", "latest offering", "let’s delve into the exciting details", "main message to take away", "make informed decisions", "mark a significant step forward", "mind-boggling figure","more robust evaluation","for instance","navigate the landscape","notably","one step closer","one thing is clear","only time will tell","opens up exciting possibilities","paving the way for enhanced performance","possibilities are endless","potentially revolutionizing the way","push the boundaries","raise fairness concerns","raise intriguing questions","rapid pace of development","rapidly developing","redefine the future","remarkable abilities","remarkable breakthrough","remarkable proficiency","remarkable success","remarkable tool","remarkably","elevate ","captivate ","tapestry ","delve ","leverage ","resonate ","foster ","endeavor ","embark ","unleash ","renowned","represent a major milestone","represents a significant milestone in the field","revolutionize the way","revolutionizing the way","risks of drawing unsupported conclusions","seeking trustworthiness","significant step forward","significant strides","the necessity of clear understanding","there is still room for improvement","transformative power","truly exciting","uncover hidden trends","understanding of the capabilities","unleashing the potential","unlocking the power","unraveling","we can improve understanding and decision-making","welcome your thoughts","what sets this apart","what’s more","with the introduction","bespoke","whimsical","meticulous","emerge","refrain","vibrant","reimagine","evolve","supercharge","pivotal"],

        # Call-to-Actions (CTAs)
        "ctas": {
            "hire": "Let’s build your project",
            "contact": "Reach out today",
            "learn_more": "See Owais’s work",
            "start": "Start your web or AI journey"
        },

        # Social Caption Templates
        "caption_templates": [
            "🚀 Another idea turned into reality. Smart tools, clean code, and clear results.",
            "🤖 Built an AI agent today that saves hours of manual work—what could it do for you?",
            "⚙️ Websites and automations that actually make life easier—not harder."
        ],

        # Support & Help Tone
        "support_tone": (
            "Helpful and straightforward. Provide clear answers without fluff, and guide the user confidently."
        ),

        # Compliance & Trust
        "data_message": (
            "Any shared data stays private and is only used for delivering requested services. "
            "Your privacy and trust come first."
        )
    }


@function_tool
def textstat_tool(content: str):
    """Analyzes readability of the content using textstat."""
    try:
        flesch_reading_ease = textstat.flesch_reading_ease(content)
        flesch_kincaid_grade = textstat.flesch_kincaid_grade(content)
        smog_index = textstat.smog_index(content)
        
        feedback = []
        if flesch_reading_ease < 60:
            feedback.append("Content is difficult to read. Aim for simpler sentences and words (Flesch Reading Ease < 60).")
        if flesch_kincaid_grade > 8:
            feedback.append("Content is written at a high reading level. Target a grade 8 or lower for broader accessibility.")
        if smog_index > 10:
            feedback.append("SMOG index indicates complex text. Simplify for better comprehension.")
        
        return {
            "flesch_reading_ease": flesch_reading_ease,
            "flesch_kincaid_grade": flesch_kincaid_grade,
            "smog_index": smog_index,
            "feedback": feedback if feedback else ["Readability is good."]
        }
    except Exception as e:
        return {"error": f"Readability analysis failed: {str(e)}"}

@function_tool
def grammar_check_tool(content: str):
    """Checks grammar and style using LanguageTool."""
    try:
        tool = LanguageTool('en-US')
        matches = tool.check(content)
        feedback = []
        
        for match in matches[:10]:
            feedback.append(f"Grammar/Style issue at '{match.context}': {match.message} (Suggested: {match.replacements[0] if match.replacements else 'review manually'})")
        
        tool.close()
        return {
            "issues_count": len(matches),
            "feedback": feedback if feedback else ["No grammar or style issues detected."]
        }
    except Exception as e:
        return {"error": f"Grammar check failed: {str(e)}"}

@function_tool
def get_stock_image_tool(keyword: str):
    """Fetches a stock image with alt text from Unsplash or Pexels."""
    try:
        url = f"https://api.pexels.com/v1/search?query={keyword}&per_page=1"
        headers = {"Authorization": os.environ['PEXELS_API_KEY']}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        photo = response.json()['photos'][0]
        return {"url": photo['src']['medium'], "alt_text": f"{keyword} stock image"}

    except:
        try:
            url = f"https://api.unsplash.com/search/photos?query={keyword}&client_id={os.environ['UNSPLASH_API_KEY']}"
            response = requests.get(url)
            response.raise_for_status()
            photo = response.json()['results'][0]
            return {"url": photo['urls']['regular'], "alt_text": f"{keyword} stock image"}
        except:
            return {"error": "Failed to fetch stock image"}

@function_tool
def generate_image_tool(keyword: str):
    """Generates an image for a blog post using Hugging Face, StarryAI, or Freepik APIs."""

    prompt = f"A professional, high-quality image for a blog post about {keyword} related to social media automation"

    # Try Hugging Face
    try:
        client = InferenceClient(
            provider="hf-inference",
            api_key=os.environ["HF_TOKEN"],
            model="black-forest-labs/FLUX.1-dev"
        )
        image = client.text_to_image(prompt)
        file_path = f"/tmp/{slugify(keyword)}_hf_image.png"
        image.save(file_path)
        return {"url": file_path, "alt_text": f"{keyword} illustration for social media automation", "source": "Hugging Face"}
    except Exception as e:
        print(f"Hugging Face failed: {str(e)}")

    # Try StarryAI
    try:
        url = "https://api.starryai.com/creations/"
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "X-API-Key": os.environ["STARRYAI_API_KEY"]
        }
        payload = {
            "model": "abstractWorld",
            "aspectRatio": "landscape",
            "highResolution": False,
            "images": 1,
            "steps": 20,
            "initialImageMode": "color",
            "prompt": prompt,
            "negativePrompt": "avoid realistic depictions"
        }
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        creation = response.json()
        creation_id = creation["id"]

        # Poll for completion
        poll_url = f"https://api.starryai.com/creations/{creation_id}"
        for _ in range(10):  # Poll up to 10 times (adjust based on typical completion time)
            time.sleep(10)  # Wait 10 seconds between polls
            poll_response = requests.get(poll_url, headers=headers)
            poll_response.raise_for_status()
            creation = poll_response.json()
            if creation["status"] == "completed" and creation["images"][0]["url"]:
                image_url = creation["images"][0]["url"]
                image_response = requests.get(image_url)
                image_response.raise_for_status()
                image = Image.open(io.BytesIO(image_response.content))
                file_path = f"/tmp/{slugify(keyword)}_starryai_image.png"
                image.save(file_path)
                return {"url": file_path, "alt_text": f"{keyword} illustration for social media automation", "source": "StarryAI"}
        return {"error": "StarryAI image generation timed out"}
    except Exception as e:
        print(f"StarryAI failed: {str(e)}")

    # Try Freepik
    try:
        url = "https://api.freepik.com/v1/ai/mystic"
        headers = {
            "x-freepik-api-key": os.environ["FREEPIC_API_KEY"],
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        payload = {
            "prompt": prompt,
            "aspect_ratio": "widescreen_16_9",
            "resolution": "2k",
            "model": "realism",
            "filter_nsfw": True,
            "styling": {
                "styles": [{"name": "professional", "strength": 100}],
                "colors": [{"color": "#4A90E2", "weight": 0.5}]  # Blue tone for brand consistency
            }
        }
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        task = response.json()
        task_id = task["task_id"]

        # Poll for completion (Freepik may require polling, as per async nature)
        poll_url = f"https://api.freepik.com/v1/ai/mystic/{task_id}"  # Adjust if Freepik provides a specific polling endpoint
        for _ in range(10):  # Poll up to 10 times
            time.sleep(10)
            poll_response = requests.get(poll_url, headers=headers)
            poll_response.raise_for_status()
            task = poll_response.json()
            if task["task_status"] == "COMPLETED" and task["generated"]:
                image_url = task["generated"][0]["url"]  # Adjust based on actual response structure
                image_response = requests.get(image_url)
                image_response.raise_for_status()
                image = Image.open(io.BytesIO(image_response.content))
                file_path = f"/tmp/{slugify(keyword)}_freepik_image.png"
                image.save(file_path)
                return {"url": file_path, "alt_text": f"{keyword} illustration for social media automation", "source": "Freepik"}
        return {"error": "Freepik image generation timed out"}
    except Exception as e:
        return {"error": f"Freepik failed: {str(e)}"}

@function_tool
async def post_to_sanity_tool(
    title: str,
    summary: str,
    content: str,
    categories: List[str],
    image_path: str,
    slug: Optional[str] = None,
    alt_text: Optional[str] = None,
    faqs: Optional[List[Any]] = None
) -> Dict[str, Any]:
    """
    Posts a blog to Sanity CMS using the updated SanityAdapter.

    Args:
        title (str): Title of the blog post.
        summary (str): Summary or description of the blog post.
        content (str): Main blog content in Markdown format (with integrated links).
        categories (List[str]): List of categories.
        image_path (str): Local path or URL of the featured image.
        slug (Optional[str]): Unique slug for the blog post URL.
        alt_text (Optional[str]): Alt text for the featured image.
        faqs (Optional[List[FAQItem]]): List of FAQs as structured objects with question and answer.

    Returns:
        Dict[str, Any]: Result containing status, post_id, image info, or errors.
    """
    try:
        # Initialize SanityAdapter
        adapter = SanityAdapter(
            project_id=os.environ['SANITY_PROJECT_ID'],
            dataset=os.environ.get('SANITY_DATASET', 'production'),
            token=os.environ['SANITY_API_TOKEN']
        )

        # --- Image Handling ---
        local_image_path = image_path
        temp_file = None
        if image_path and image_path.startswith('http'):
            clean_image_url = image_path.split('?', 1)[0]
            response = requests.get(clean_image_url)
            response.raise_for_status()
            ext = os.path.splitext(clean_image_url)[-1] or '.jpg'
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(response.content)
                local_image_path = tmp.name
                temp_file = tmp.name

        logger.info(f"Passing local_image_path to SanityAdapter: {local_image_path}")
        logger.info(f"File exists at local_image_path: {os.path.exists(local_image_path)}")
        if os.path.exists(local_image_path):
            logger.info(f"Size of file at local_image_path: {os.path.getsize(local_image_path)} bytes")
        else:
            logger.error("File does not exist at local_image_path!")

        # Convert FAQItem objects or dictionaries to plain dictionaries for SanityAdapter
        faqs_list = []
        if faqs:
            for faq in faqs:
                if hasattr(faq, 'dict'):  # Pydantic model
                    faq_dict = faq.dict()
                    if 'question' in faq_dict and 'answer' in faq_dict:
                        faqs_list.append(faq_dict)
                elif isinstance(faq, dict):  # Regular dictionary
                    if 'question' in faq and 'answer' in faq:
                        faqs_list.append(faq)
                else:
                    logger.warning(f"Skipping invalid FAQ item: {faq}")
        
        # --- Sanity CMS Posting ---
        result = adapter.post_blog(
            title=title,
            summary=summary,
            content=content,
            categories=categories,
            local_image_path=local_image_path,
            slug=slug,
            alt_text=alt_text,
            faqs=faqs_list
        )

        # --- Cleanup ---
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)

        # --- Return Result ---
        if result["status"] == "success":
            return {
                "status": "success",
                "post_id": result["post_id"],
                "image_id": result.get("image_id"),
                "image_url": result.get("image_url"),
                "image_source": "Downloaded" if image_path.startswith('http') else "Local",
                "image_alt_text": alt_text or f"{title} image",
                "notes": "Post created successfully in Sanity CMS."
            }
        else:
            return {
                "status": "error",
                "post_id": None,
                "image_id": result.get("image_id"),
                "image_url": result.get("image_url"),
                "image_source": "None" if not image_path else ("Download Failed" if image_path.startswith('http') else "Local Error"),
                "image_alt_text": alt_text or f"{title} image",
                "error": f"Failed to post to Sanity: {result.get('error', 'Unknown error from adapter')}"
            }

    except Exception as e:
        if 'temp_file' in locals() and temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        return {
            "status": "error",
            "post_id": None,
            "image_id": None,
            "image_url": None,
            "image_source": "None",
            "image_alt_text": alt_text or f"{title} image",
            "error": f"Unexpected error in post_to_sanity_tool: {str(e)}"
        }

@function_tool
async def fetch_internal_links_tool(topic: str, max_results: int = 3, exclude_slug: str = None):
    """
    Fetches related posts from Sanity CMS for a given topic to create internal links.
    Args:
        topic: Topic for the topic cluster (e.g., "What is AI agents").
        max_results: Maximum number of posts to return (default: 3).
        exclude_slug: Slug of the current post to exclude.
    Returns:
        dict: Result containing status, links, and message or error.
    """
    try:
        adapter = SanityAdapter(
            project_id=os.environ['SANITY_PROJECT_ID'],
            dataset=os.environ.get('SANITY_DATASET', 'production'),
            token=os.environ['SANITY_API_TOKEN']
        )
        links = adapter.fetch_internal_links(topic=topic, max_results=max_results, exclude_slug=exclude_slug)
        return {
            "status": "success",
            "links": links,
            "message": f"Fetched {len(links)} internal links for topic '{topic}'."
        }
    except Exception as e:
        logger.error(f"Failed to fetch internal links: {e}")
        return {
            "status": "error",
            "links": [],
            "error": f"Failed to fetch internal links: {str(e)}"
        }