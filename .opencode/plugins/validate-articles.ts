/**
 * OpenCode Plugin: Auto-validate knowledge articles after write/edit.
 *
 * Listens to tool.execute.after events. When the agent writes or edits
 * a file matching knowledge/articles/*.json, this plugin automatically
 * runs hooks/validate_json.py against it and logs the result.
 *
 * IMPORTANT:
 * - Uses .nothrow() instead of .quiet() to avoid blocking OpenCode.
 * - All shell calls are wrapped in try/catch to prevent unhandled exceptions.
 */
import type { Plugin } from "@opencode-ai/plugin"

const ARTICLES_PATTERN = /knowledge\/articles\/.*\.json$/

export default ((({ directory, $ }) => {
  return {
    "tool.execute.after": async (input, _output) => {
      // Only trigger on write or edit tools
      const tool = input.tool
      if (tool !== "write" && tool !== "edit") {
        return
      }

      // Extract file path from tool args
      const filePath: string | undefined =
        input.args?.filePath || input.args?.file_path
      if (!filePath) {
        return
      }

      // Only validate knowledge article JSON files
      if (!ARTICLES_PATTERN.test(filePath)) {
        return
      }

      // Run validation script
      try {
        const result = await $`python3 hooks/validate_json.py ${filePath}`
          .nothrow()
          .cwd(directory)

        if (result.exitCode === 0) {
          console.log(`[validate-articles] PASS: ${filePath}`)
        } else {
          console.warn(
            `[validate-articles] FAIL: ${filePath}\n${result.stdout}${result.stderr}`,
          )
        }
      } catch (err) {
        console.error(
          `[validate-articles] Error running validation: ${err}`,
        )
      }

      // Run quality check
      try {
        const result = await $`python3 hooks/check_quality.py ${filePath}`
          .nothrow()
          .cwd(directory)

        if (result.exitCode === 0) {
          console.log(
            `[validate-articles] Quality OK:\n${result.stdout}`,
          )
        } else {
          console.warn(
            `[validate-articles] Quality issues:\n${result.stdout}${result.stderr}`,
          )
        }
      } catch (err) {
        console.error(
          `[validate-articles] Error running quality check: ${err}`,
        )
      }
    },
  }
}) satisfies Plugin)
