import Link from '@mui/material/Link';
import DOMPurify from 'dompurify';
import parse, { domToReact } from 'html-react-parser';

/**
 * Sanitise HTML content
 * This will also transform <a> tags into Material UI <Link> tags
 * @param {rawHTML} rawHTML - Dirty HTML to render 
 * @returns Sanitised HTML Component
 */
const RemoveDangerousHTML = ({ rawHTML }) => {
	const sanitised = DOMPurify.sanitize(rawHTML, { USE_PROFILES: { html: true }});

	return parse(sanitised, {
		replace: node => {
			if(node.type == "tag" && node.tagName === "a") {
				return <Link {...node.attribs} rel="noopener" sx={{ color: 'inherit', fontWeight: 'bold' }}>{domToReact(node.children)}</Link>
			}
		}
	})
}

export default RemoveDangerousHTML;