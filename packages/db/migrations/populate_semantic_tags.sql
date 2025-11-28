-- Populate semantic_tags for existing keywords based on the original categorization
-- Run this after adding the semantic_tags column

-- Academic titles and credentials
UPDATE keyword_stats SET semantic_tags = ARRAY['#academia', '#credentials'] WHERE keyword IN ('professor', 'phd', 'postdoc', 'lecturer', 'academic', 'faculty', 'tenure', 'emeritus');

-- Research roles
UPDATE keyword_stats SET semantic_tags = ARRAY['#academia', '#research'] WHERE keyword IN ('researcher', 'scientist', 'scholar', 'principal investigator', 'research fellow', 'doctoral candidate', 'research associate');

-- Social sciences
UPDATE keyword_stats SET semantic_tags = ARRAY['#academia', '#social-science'] WHERE keyword IN ('sociologist', 'anthropologist', 'psychologist', 'political scientist', 'economist', 'geographer', 'demographer');

-- Health and medical research
UPDATE keyword_stats SET semantic_tags = ARRAY['#academia', '#health', '#medical'] WHERE keyword IN ('epidemiologist', 'public health researcher', 'health services research', 'clinical researcher', 'bioethicist', 'medical anthropology', 'health policy', 'psychiatry', 'neuroscience', 'immunologist', 'oncologist');

-- Industry/applied research
UPDATE keyword_stats SET semantic_tags = ARRAY['#industry', '#applied-research'] WHERE keyword IN ('pharma', 'biotech researcher', 'UX researcher', 'market researcher', 'policy analyst');

-- Research indicators/credentials
UPDATE keyword_stats SET semantic_tags = ARRAY['#credentials', '#publications'] WHERE keyword IN ('peer reviewed', 'published author', 'grant funded', 'NIH funded', 'NSF funded', 'h-index');

-- Qualitative research methods
UPDATE keyword_stats SET semantic_tags = ARRAY['#academia', '#qualitative'] WHERE keyword IN ('ethnography', 'grounded theory', 'narrative inquiry', 'discourse analysis', 'thematic analysis', 'case study research', 'interpretive research');

-- Humanities and cultural studies
UPDATE keyword_stats SET semantic_tags = ARRAY['#academia', '#humanities'] WHERE keyword IN ('philosopher', 'historian', 'literary scholar', 'cultural studies', 'gender studies', 'postcolonial studies', 'media studies', 'science studies', 'STS researcher');

-- Interdisciplinary/other
UPDATE keyword_stats SET semantic_tags = ARRAY['#academia', '#interdisciplinary'] WHERE keyword IN ('bioinformatician', 'education researcher', 'curriculum studies', 'higher education');

-- Verify: Show keywords that may have been missed
-- SELECT keyword FROM keyword_stats WHERE semantic_tags IS NULL OR semantic_tags = '{}';
