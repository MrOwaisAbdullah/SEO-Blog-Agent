"""
Enhanced Markdown to Sanity Portable Text Converter
With comprehensive styling support, image handling, and debugging
"""
import uuid
import logging
from typing import List, Dict, Any, Optional
import commonmark
import html
import re

logger = logging.getLogger(__name__)

class MarkdownToSanityConverter:
    """Converts Markdown to Sanity Portable Text format with debugging."""
    
    def __init__(self, debug=False):
        self.blocks: List[Dict[str, Any]] = []
        self.debug = debug
        self.mark_def_counter = 0
    
    def _create_block_key(self) -> str:
        """Generate a unique key for Sanity blocks/spans."""
        return str(uuid.uuid4())
    
    def _get_next_mark_key(self) -> str:
        """Generate a unique key for markDefs like links."""
        self.mark_def_counter += 1
        return f"mark-def-{self.mark_def_counter}"
    
    def _debug_log(self, message: str):
        """Log debug messages if debug mode is enabled."""
        if self.debug:
            print(f"DEBUG: {message}")
    
    def _walk_ast_and_print(self, node, depth=0):
        """Debug function to print AST structure."""
        if not self.debug:
            return
        indent = "  " * depth
        node_info = f"{indent}{node.t}"
        if hasattr(node, 'literal') and node.literal:
            node_info += f" -> '{node.literal[:50]}'"
        if hasattr(node, 'level'):
            node_info += f" (level: {node.level})"
        if hasattr(node, 'destination'):
            node_info += f" (href: {node.destination})"
        print(node_info)
        
        # Walk children
        child = node.first_child
        while child:
            self._walk_ast_and_print(child, depth + 1)
            child = child.nxt
    
    def _extract_all_text_from_node(self, node) -> str:
        """Extract all text content from a node and its children."""
        if not node:
            return ""
        text_parts = []
        
        if node.t == 'text' and hasattr(node, 'literal'):
            text_parts.append(node.literal or "")
        elif node.t in ['softbreak', 'linebreak']:
            text_parts.append(" ")
        
        # Process children
        child = node.first_child
        while child:
            text_parts.append(self._extract_all_text_from_node(child))
            child = child.nxt
            
        return "".join(text_parts)
    
    def _create_simple_span(self, text: str, marks: Optional[List[str]] = None):
        """Create a simple span with text and marks."""
        if not text:
            return None
            
        span = {
            "_key": self._create_block_key(),
            "_type": "span",
            "text": str(text)
        }
        
        if marks and len(marks) > 0:
            span["marks"] = [mark for mark in marks if mark]
            
        return span
    
    def _parse_inline_content(self, node) -> tuple[List[Dict], List[Dict]]:
        """Parse inline content and return spans and mark definitions."""
        spans = []
        mark_defs = []
        
        def process_node_inline(current_node, current_marks=None):
            if current_marks is None:
                current_marks = []
            if not current_node:
                return
                
            node_type = current_node.t
            self._debug_log(f"Processing inline node: {node_type}")
            
            if node_type == 'text':
                text = getattr(current_node, 'literal', '')
                if text:
                    span = self._create_simple_span(text, current_marks.copy())
                    if span:
                        spans.append(span)
                        
            elif node_type in ['softbreak', 'linebreak']:
                text = ' ' if node_type == 'softbreak' else '\n'
                span = self._create_simple_span(text, current_marks.copy())
                if span:
                    spans.append(span)
                    
            elif node_type == 'strong':
                new_marks = current_marks.copy()
                new_marks.append('strong')
                child = current_node.first_child
                while child:
                    process_node_inline(child, new_marks)
                    child = child.nxt
                    
            elif node_type == 'emph':
                new_marks = current_marks.copy()
                new_marks.append('em')
                child = current_node.first_child
                while child:
                    process_node_inline(child, new_marks)
                    child = child.nxt
                    
            elif node_type == 'code':
                text = getattr(current_node, 'literal', '')
                if text:
                    new_marks = current_marks.copy()
                    new_marks.append('code')
                    span = self._create_simple_span(text, new_marks)
                    if span:
                        spans.append(span)
                        
            elif node_type == 'link':
                destination = getattr(current_node, 'destination', '')
                title = getattr(current_node, 'title', '')
                # Validate that destination is a proper URL
                if destination:
                    # Check if URL is properly formatted
                    if not destination.startswith(('http://', 'https://', '/', '#', 'mailto:', 'tel:')):
                        # If not a proper URL, treat as text instead of link
                        child = current_node.first_child
                        while child:
                            process_node_inline(child, current_marks)
                            child = child.nxt
                    else:
                        mark_key = self._get_next_mark_key()
                        mark_def = {
                            "_key": mark_key,
                            "_type": "link",
                            "href": destination
                        }
                        if title:
                            mark_def["title"] = title
                        mark_defs.append(mark_def)
                        
                        new_marks = current_marks.copy()
                        new_marks.append(mark_key)
                        child = current_node.first_child
                        while child:
                            process_node_inline(child, new_marks)
                            child = child.nxt
                else:
                    # Process children without link mark
                    child = current_node.first_child
                    while child:
                        process_node_inline(child, current_marks)
                        child = child.nxt
                        
            elif node_type == 'strikethrough':
                new_marks = current_marks.copy()
                new_marks.append('strike-through')
                child = current_node.first_child
                while child:
                    process_node_inline(child, new_marks)
                    child = child.nxt
                    
            elif node_type == 'html_inline':
                literal = getattr(node, 'literal', '')
                if literal and isinstance(literal, str):  # Add null and type check
                    if literal.startswith('<u>') and literal.endswith('</u>'):
                        text = literal[3:-4]
                        new_marks = current_marks.copy()
                        new_marks.append('underline')
                        span = self._create_simple_span(text, new_marks)
                        if span:
                            spans.append(span)
                    elif literal.startswith('<s>') and literal.endswith('</s>'):
                        text = literal[2:-3]
                        new_marks = current_marks.copy()
                        new_marks.append('strike-through')
                        span = self._create_simple_span(text, new_marks)
                        if span:
                            spans.append(span)
                    elif literal.startswith('<del>') and literal.endswith('</del>'):
                        text = literal[5:-6]
                        new_marks = current_marks.copy()
                        new_marks.append('strike-through')
                        span = self._create_simple_span(text, new_marks)
                        if span:
                            spans.append(span)
                    else:
                        # If it's HTML but not a supported tag, treat as regular text
                        span = self._create_simple_span(literal, current_marks.copy())
                        if span:
                            spans.append(span)
                elif literal:  # Handle non-string literals
                    span = self._create_simple_span(str(literal), current_marks.copy())
                    if span:
                        spans.append(span)
                        
            elif node_type == 'image':
                # Process inline images (images within text paragraphs)
                destination = getattr(current_node, 'destination', '')
                title = getattr(current_node, 'title', '')
                alt_text = ''
                
                # Extract alt text from the first child if it's text
                if current_node.first_child and current_node.first_child.t == 'text':
                    alt_text = getattr(current_node.first_child, 'literal', '')
                
                if destination:
                    # Add image as a separate block rather than inline content as per Sanity's Portable Text spec
                    # However, if the image is in the middle of a paragraph, we'll create a placeholder
                    image_placeholder = f'![{alt_text}]({destination} "{title}")'
                    span = self._create_simple_span(image_placeholder, current_marks.copy())
                    if span:
                        spans.append(span)
                        
            else:
                # For unknown inline types, process children
                child = current_node.first_child
                while child:
                    process_node_inline(child, current_marks)
                    child = child.nxt
        
        # Start processing from the given node
        if node.t in ['paragraph', 'heading', 'block_quote']:
            # Process children of block elements
            child = node.first_child
            while child:
                process_node_inline(child)
                child = child.nxt
        else:
            # Process the node itself
            process_node_inline(node)
            
        self._debug_log(f"Generated {len(spans)} spans and {len(mark_defs)} mark definitions")
        return spans, mark_defs
    
    def _create_block(self, style: str, spans: List[Dict], mark_defs: List[Dict] = None):
        """Create a Sanity block."""
        return {
            "_key": self._create_block_key(),
            "_type": "block",
            "children": spans or [],
            "markDefs": mark_defs or [],
            "style": style
        }
    
    def _process_node(self, node):
        """Process a single node and convert to Sanity blocks."""
        if not node:
            return
            
        node_type = node.t
        self._debug_log(f"Processing block node: {node_type}")
        
        if node_type == 'document':
            # Process all children
            child = node.first_child
            while child:
                self._process_node(child)
                child = child.nxt
                
        elif node_type == 'paragraph':
            # Check if this paragraph contains only an image
            child = node.first_child
            if (child and child.t == 'image' and 
                not child.nxt and  # Only child
                hasattr(child, 'destination')):
                # This is an image paragraph, create an image block
                self._create_image_block(child)
            else:
                # Regular paragraph
                spans, mark_defs = self._parse_inline_content(node)
                if spans:
                    block = self._create_block('normal', spans, mark_defs)
                    self.blocks.append(block)
                    self._debug_log(f"Added paragraph block with {len(spans)} spans")
                else:
                    # Fallback: extract all text as plain text
                    text = self._extract_all_text_from_node(node)
                    if text.strip():
                        fallback_span = self._create_simple_span(text.strip())
                        if fallback_span:
                            block = self._create_block('normal', [fallback_span])
                            self.blocks.append(block)
                            self._debug_log(f"Added fallback paragraph block")
                            
        elif node_type == 'heading':
            level = getattr(node, 'level', 1)
            style = f"h{min(max(level, 1), 6)}"
            spans, mark_defs = self._parse_inline_content(node)
            if spans:
                block = self._create_block(style, spans, mark_defs)
                self.blocks.append(block)
                self._debug_log(f"Added {style} heading block with {len(spans)} spans")
            else:
                # Fallback for headings
                text = self._extract_all_text_from_node(node)
                if text.strip():
                    fallback_span = self._create_simple_span(text.strip())
                    if fallback_span:
                        block = self._create_block(style, [fallback_span])
                        self.blocks.append(block)
                        self._debug_log(f"Added fallback {style} heading block")
                        
        elif node_type == 'block_quote':
            spans, mark_defs = self._parse_inline_content(node)
            if spans:
                block = self._create_block('blockquote', spans, mark_defs)
                self.blocks.append(block)
                self._debug_log(f"Added blockquote block")
            else:
                text = self._extract_all_text_from_node(node)
                if text.strip():
                    fallback_span = self._create_simple_span(text.strip())
                    if fallback_span:
                        block = self._create_block('blockquote', [fallback_span])
                        self.blocks.append(block)
                        
        elif node_type == 'code_block':
            literal = getattr(node, 'literal', '')
            info = getattr(node, 'info', '')
            if literal is not None:
                code_text = literal.rstrip('\n') if isinstance(literal, str) else str(literal)
                if code_text:
                    span = self._create_simple_span(code_text, ['code'])
                    if span:
                        block = self._create_block('normal', [span])
                        if info:  # Preserve language information
                            block["language"] = info.split()[0] if info else "text"
                        self.blocks.append(block)
                        self._debug_log(f"Added code block")
                        
        elif node_type == 'list':
            # Process list items with proper list type detection
            list_type = getattr(node, 'list_type', 'bullet')  # 'bullet' or 'ordered'
            child = node.first_child
            while child:
                self._process_node(child)
                child = child.nxt
                
        elif node_type == 'item':
            spans, mark_defs = self._parse_inline_content(node)
            if spans:
                # Use Sanity's native list properties instead of manual bullets
                style = "normal"
                list_item = None
                
                # Detect parent list type
                parent = node.parent
                if parent and parent.t == 'list':
                    list_type = getattr(parent, 'list_type', 'bullet')
                    if list_type == 'ordered':
                        list_item = "number"
                    else:
                        list_item = "bullet"
                
                block = self._create_block(style, spans, mark_defs)
                if list_item:
                    block["listItem"] = list_item
                    
                self.blocks.append(block)
                self._debug_log(f"Added list item block")
            else:
                # Fallback for list items
                text = self._extract_all_text_from_node(node)
                if text.strip():
                    text_span = self._create_simple_span(text.strip())
                    if text_span:
                        block = self._create_block('normal', [text_span])
                        self.blocks.append(block)
                        
        elif node_type == 'thematic_break':
            span = self._create_simple_span('---')
            if span:
                block = self._create_block('normal', [span])
                self.blocks.append(block)
                self._debug_log(f"Added thematic break block")
                
        elif node_type == 'image':
            # Handle standalone image nodes
            self._create_image_block(node)
                
        elif node_type == 'link':
            # Handle standalone link nodes (though these should be rare in CommonMark)
            # Usually links will be processed as inline elements within blocks
            spans, mark_defs = self._parse_inline_content(node)
            if spans:
                block = self._create_block('normal', spans, mark_defs)
                self.blocks.append(block)
                
        else:
            # For unknown block types, try to extract text
            text = self._extract_all_text_from_node(node)
            if text.strip():
                span = self._create_simple_span(text.strip())
                if span:
                    block = self._create_block('normal', [span])
                    self.blocks.append(block)
                    self._debug_log(f"Added unknown block type: {node_type}")
    
    def _create_image_block(self, image_node):
        """Create a Sanity image block from a markdown image node."""
        destination = getattr(image_node, 'destination', '')
        title = getattr(image_node, 'title', '')
        alt_text = ''
        
        # Extract alt text from the first child if it's text
        if image_node.first_child and image_node.first_child.t == 'text':
            alt_text = getattr(image_node.first_child, 'literal', '')
        
        if destination:
            # Create image block with URL directly (Sanity adapter will process this)
            image_block = {
                "_key": self._create_block_key(),
                "_type": "image",
                "asset": {
                    "url": destination
                }
            }
            
            # Add alt text if available
            if alt_text:
                image_block["alt"] = alt_text
                
            # Add title if available
            if title:
                image_block["title"] = title
                
            self.blocks.append(image_block)
            self._debug_log(f"Added image block: {destination}")
    
    def convert(self, markdown_text: str, debug: bool = False) -> List[Dict[str, Any]]:
        """Convert markdown text to Sanity blocks."""
        if not markdown_text or not isinstance(markdown_text, str) or not markdown_text.strip():
            return []
            
        self.debug = debug
        try:
            # Reset state
            self.blocks = []
            self.mark_def_counter = 0
            self._debug_log(f"Starting conversion of {len(markdown_text)} characters")
            
            # Clean the content by removing leading whitespace from each line
            if markdown_text:
                cleaned_lines = [line.lstrip() for line in markdown_text.split('\n')]
                cleaned_content = '\n'.join(cleaned_lines).strip()
            else:
                cleaned_content = ""
                
            self._debug_log(f"Original content length: {len(markdown_text)}")
            self._debug_log(f"Cleaned content length: {len(cleaned_content)}")
            
            # Parse markdown with CommonMark
            parser = commonmark.Parser()
            ast = parser.parse(cleaned_content)
            
            if self.debug:
                print("\n=== AST STRUCTURE ===")
                self._walk_ast_and_print(ast)
                print("=== END AST ===\n")
            
            # Convert to Sanity blocks
            if ast:
                self._process_node(ast)
                
            self._debug_log(f"Successfully converted to {len(self.blocks)} Sanity blocks")
            
            if len(self.blocks) == 0:
                self._debug_log("No blocks generated, creating fallback")
                # Create fallback block with original text
                fallback_span = self._create_simple_span(markdown_text)
                if fallback_span:
                    fallback_block = self._create_block('normal', [fallback_span])
                    self.blocks.append(fallback_block)
                    
            return self.blocks
            
        except Exception as e:
            logger.error(f"Error converting markdown to Sanity blocks: {e}", exc_info=True)
            # Return error block
            error_span = self._create_simple_span(f"[Conversion Error: {str(e)}]")
            if error_span:
                error_block = self._create_block('normal', [error_span])
                return [error_block]
            return []

def markdown_to_sanity_blocks(markdown_text: str, debug: bool = False) -> List[Dict[str, Any]]:
    """
    Convert Markdown text to Sanity's blockContent (Portable Text) structure.
    
    Args:
        markdown_text (str): The markdown content to convert
        debug (bool): Enable debug logging to see what's happening
        
    Returns:
        List[Dict[str, Any]]: List of Sanity block objects
    """
    converter = MarkdownToSanityConverter(debug=debug)
    return converter.convert(markdown_text, debug=debug)

# Example usage and testing
if __name__ == "__main__":
    import json
    
    # Test markdown with all supported features
    test_markdown = """# Main Heading
This is a **bold** paragraph with *italic* text and a ~~strikethrough~~ and <u>underline</u>.

## Subheading
Here's a list:
- Item 1 with `inline code`
- Item 2 with more text

![Alt text for image](https://example.com/test-image.jpg "Image title")

> This is a blockquote with **bold** text.

Final paragraph with some text.

![Another image](/local/path/image.png)

```python
a + b = c
a = 10
```
"""
    
    print("Testing markdown conversion with images...")
    blocks = markdown_to_sanity_blocks(test_markdown, debug=True)
    print(f"\nGenerated {len(blocks)} blocks:")
    print(json.dumps(blocks, indent=2))