/* Local text evidence, not an employer's ATS or a hiring decision. */
(function (root) {
  "use strict";
  function norm(text) { return String(text || "").toLowerCase().replace(/[–—]/g, "-").replace(/\s+/g, " ").trim(); }
  function escapeRegex(text) { return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  function create(catalog) {
    var groups = catalog.skills.map(function (g) {
      return { name: g.name, patterns: g.aliases.map(function (alias) {
        return new RegExp("(^|[^a-z0-9])(" + escapeRegex(norm(alias)) + ")(?=$|[^a-z0-9])", "g");
      }) };
    });
    var credentials = /^(ca|cfa|cma|cpa|acca|frm|nism)$/;
    function skillsIn(text, resumeMode) {
      var low = norm(text);
      return groups.filter(function (g) {
        return g.patterns.some(function (pattern) {
          pattern.lastIndex = 0;
          var match;
          while ((match = pattern.exec(low))) {
            var start = match.index + match[1].length;
            var before = low.slice(Math.max(0, start - 65), start);
            var after = low.slice(start + match[2].length, start + match[2].length + 65);
            if (credentials.test(g.name) && /^\s*(?:[-:]\s*)?(inter\b|intermediate\b|finalist\b|final\b|candidate\b|level\s*[123i]|part\s*[123i])/.test(after)) continue;
            // Do not turn explicit absence or a pending credential into evidence.
            if (resumeMode && /\b(no|not|without|lack of|lacking)\s+(?:(?:any|prior|hands-on|working|experience|knowledge|proficient|certified|familiar|exposure|to|of|in|with)\s+)*$/.test(before)) continue;
            if (resumeMode && /^\s*:?\s*(?:(?:experience|knowledge)\s+)?(?:not held|not completed|not certified|not experienced|no experience|no knowledge)\b/.test(after)) continue;
            if (resumeMode && credentials.test(g.name) && (
              /\b(pursuing|studying for|candidate for|semi-qualified|semi qualified|preparing for)\s*$/.test(before) ||
              /^\s*(?:[-:]\s*)?(inter\b|intermediate\b|finalist\b|final\b|candidate\b|level\s*[123i]|part\s*[123i]|pursuing\b|in progress\b|pending\b)/.test(after)
            )) continue;
            return true;
          }
          return false;
        });
      }).map(function (g) { return g.name; });
    }

    function clauses(text) {
      // Protect common credential abbreviations before splitting sentences.
      return String(text || "").replace(/\b([bm])\.com\b/gi, "$1com")
        .split(/\n+|[;!?•]+|\.\s+(?=[A-Z])/).map(function (s) { return s.trim(); }).filter(Boolean);
    }
    function assess(job, resumeText) {
      var evidence = skillsIn(resumeText, true);
      var seen = {};
      var checks = [];
      var text = [job.requirement_text, job.jd].filter(Boolean).join("\n");
      var heading = "", headingLines = 0;
      clauses(text).forEach(function (line) {
        var low = norm(line);
        if (/^(required|mandatory|essential|preferred|desirable)(?: skills| qualifications| requirements)?\s*:?$/.test(low)) {
          heading = /^(preferred|desirable)/.test(low) ? "preferred" : "required";
          headingLines = 0;
          return;
        }
        if (/^(responsibilities|about us|benefits|what we offer|job description)\s*:?$/.test(low) || ++headingLines > 12) heading = "";
        // Negative requirements are not blockers. Mixed/long clauses need review.
        if (/\b(not required|not mandatory|no .{0,35} required|no prior experience|not essential|need not)\b/.test(low)) return;
        var required = /\b(must(?: have)?|mandatory|required|essential|prerequisite|minimum qualification)\b/.test(low);
        var preferred = /\b(preferred|preferable|desirable|advantage|nice to have|nice-to-have|a plus)\b/.test(low);
        if (!required && !preferred) { required = heading === "required"; preferred = heading === "preferred"; }
        if (!required && !preferred) return;
        var skills = skillsIn(line, false);
        if (!skills.length) return;
        var alternative = /\bor\b|\band\/or\b|\bequivalent\b|\//.test(low.replace(/s\/4hana/g, ""));
        var ambiguous = (required && preferred) || line.length > 450 || alternative;
        // Alternatives are not automatically satisfied: review the entire clause.
        var present = skills.filter(function (s) { return evidence.indexOf(s) >= 0; });
        var absent = skills.filter(function (s) { return evidence.indexOf(s) < 0; });
        var kind = ambiguous ? "verify" : preferred ? "preferred" : "required";
        var key = norm(line);
        if (seen[key]) return;
        seen[key] = true;
        checks.push({ kind: kind, skills: skills, present: present, absent: absent,
          evidence: (heading ? heading.charAt(0).toUpperCase() + heading.slice(1) + " section: " : "") + line,
          status: absent.length ? "Not evidenced in resume" : "Mentioned in resume — verify depth" });
      });
      var manual = [];
      if (job.experience) manual.push("Experience requested: " + job.experience + ". Check relevant domain experience, not just total years.");
      var manualRules = [
        [/\b(notice period|immediate join|joining|join within|availability)\b/i, "Joining date / notice period"],
        [/\b(night shift|rotational shift|rotating shift|us shift|uk shift|weekend|work from office|onsite|on-site|relocation)\b/i, "Shift, workplace or relocation"],
        [/\b(work authori[sz]ation|right to work|visa|sponsorship)\b/i, "Work authorization"],
        [/\b(degree|qualification|certification|licen[cs]e|qualified|graduate)\b/i, "Qualification or certification"],
        [/\b(years?|yrs?)\b/i, "Relevant experience"]
      ];
      manualRules.forEach(function (rule) {
        var line = clauses(text).find(function (s) { return rule[0].test(s); });
        if (line) manual.push(rule[1] + ": " + line);
      });
      return { checks: checks, manual: manual, evidence: evidence,
        requiredGaps: checks.filter(function (c) { return c.kind === "required" && c.absent.length; }),
        limited: !job.has_text || job.jd_truncated !== false };
    }
    return { skillsIn: skillsIn, assess: assess };
  }
  root.FinanceMatch = { create: create };
})(typeof globalThis !== "undefined" ? globalThis : this);
