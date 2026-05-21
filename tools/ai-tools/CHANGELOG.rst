**0.7.0 - 05/21/26**

 - Add ``environments`` skill covering env discovery and creation across vivarium repos

**0.6.0 - 05/20/26**

 - Add ``pytest`` skill covering vivarium pytest conventions, markers, and scope expansion
 - Add ``framework-clis`` skill covering vivarium console scripts on PATH in a model-repo env
 
**0.5.1 - 05/19/26**

- Add LICENSE file
- Remove .gitignore file

**0.5.0 - 05/18/26**

 - Add ``team-conventions`` skill covering SimSci Engineering conventions
 - Add dependency for anthropic slack plugin
 - Add make command skill

**0.4.0 - 05/13/26**

 - Add ``continuous-integration`` and ``plugin-setup`` skills covering install and interaction
   with the SimSci Jenkins MCP server.

**0.3.2- 05/13/26**

- Put the marketplace at the repo root
- Add dependency for anthropic skill-creator

**0.3.1 - 05/13/26**

- Fix marketplace directory path for monorepo

**0.3.0 - 05/12/26**

 - Migrate from standalone ``ihmeuw/vivarium_ai_tools`` repo into the
   ``vivarium-suite`` monorepo under ``tools/ai-tools/``. Plugin marketplace
   install path has changed; see README for new instructions.

**0.2.0**

 - Restructure as a Claude Code plugin with a self-hosted marketplace.
 - Better restrict tool invocation frontmatter
 - Restructure sub-agent delegation to match claude and copilot-specific architecture

**0.1.0 - 7/29/25**

 - Initial repository setup
