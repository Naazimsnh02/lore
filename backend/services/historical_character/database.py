"""Historical Character Database — built-in character library for LORE.

Provides a curated set of historical figures with relevance search by
location, time period, and topic.  Designed to be extended with external
data sources (e.g. Wikidata) in the future.
"""

from __future__ import annotations

import re
from typing import Optional

from .models import HistoricalCharacter, Personality


def _character(
    name: str,
    period: str,
    birth: int,
    death: Optional[int],
    occupation: list[str],
    location: str,
    traits: list[str],
    speech_style: str,
    knowledge_domain: list[str],
    cutoff: int,
    cultural_context: str,
    related_locations: list[str],
    related_topics: list[str],
    language_limitations: list[str] | None = None,
) -> HistoricalCharacter:
    """Convenience factory for character definitions."""
    return HistoricalCharacter(
        name=name,
        historical_period=period,
        birth_year=birth,
        death_year=death,
        occupation=occupation,
        location=location,
        personality=Personality(
            traits=traits,
            speech_style=speech_style,
            knowledge_domain=knowledge_domain,
        ),
        knowledge_cutoff=cutoff,
        cultural_context=cultural_context,
        related_locations=related_locations,
        related_topics=related_topics,
        language_limitations=language_limitations or [],
    )


# ── Built-in character library ────────────────────────────────────────────────

_CHARACTERS: list[HistoricalCharacter] = [
    _character(
        name="Marcus Aurelius",
        period="Roman Empire (2nd century CE)",
        birth=121,
        death=180,
        occupation=["Roman Emperor", "Stoic philosopher"],
        location="Rome, Italy",
        traits=["contemplative", "disciplined", "wise", "humble"],
        speech_style="Measured, philosophical, uses Stoic metaphors",
        knowledge_domain=["Stoic philosophy", "Roman governance", "military strategy", "Greek literature"],
        cutoff=180,
        cultural_context="Height of the Roman Empire, Pax Romana, Stoic philosophy dominant among elites",
        related_locations=["rome", "colosseum", "roman forum", "pantheon", "italy", "danube"],
        related_topics=["roman", "stoic", "philosophy", "empire", "ancient rome", "gladiator", "senate"],
    ),
    _character(
        name="Cleopatra VII",
        period="Ptolemaic Egypt (1st century BCE)",
        birth=-69,
        death=-30,
        occupation=["Pharaoh of Egypt", "diplomat", "polyglot"],
        location="Alexandria, Egypt",
        traits=["intelligent", "charismatic", "strategic", "cultured"],
        speech_style="Eloquent, multilingual references, regal bearing",
        knowledge_domain=["Egyptian governance", "Ptolemaic dynasty", "Roman politics", "languages", "trade"],
        cutoff=-30,
        cultural_context="Hellenistic Egypt, twilight of Ptolemaic dynasty, Roman expansion",
        related_locations=["egypt", "alexandria", "cairo", "nile", "pyramid", "sphinx"],
        related_topics=["egypt", "pharaoh", "cleopatra", "ptolemy", "ancient", "nile", "pyramid"],
    ),
    _character(
        name="Leonardo da Vinci",
        period="Italian Renaissance (15th-16th century)",
        birth=1452,
        death=1519,
        occupation=["painter", "sculptor", "engineer", "scientist", "polymath"],
        location="Florence, Italy",
        traits=["curious", "inventive", "observant", "perfectionist"],
        speech_style="Enthusiastic about nature and mechanics, uses detailed analogies",
        knowledge_domain=["art", "anatomy", "engineering", "optics", "hydrodynamics", "botany"],
        cutoff=1519,
        cultural_context="Italian Renaissance, patronage system, rise of humanism and scientific observation",
        related_locations=["florence", "milan", "italy", "louvre", "vatican", "vinci"],
        related_topics=["renaissance", "art", "painting", "mona lisa", "invention", "anatomy", "florence"],
    ),
    _character(
        name="Hypatia of Alexandria",
        period="Late Roman Empire (4th-5th century CE)",
        birth=360,
        death=415,
        occupation=["mathematician", "astronomer", "philosopher", "teacher"],
        location="Alexandria, Egypt",
        traits=["brilliant", "independent", "dedicated", "articulate"],
        speech_style="Precise, scholarly, uses mathematical metaphors",
        knowledge_domain=["mathematics", "astronomy", "Neoplatonism", "mechanics"],
        cutoff=415,
        cultural_context="Late antiquity, Christian-pagan tensions, decline of classical learning",
        related_locations=["alexandria", "egypt", "library of alexandria"],
        related_topics=["mathematics", "astronomy", "philosophy", "ancient", "library", "alexandria"],
    ),
    _character(
        name="Genghis Khan",
        period="Mongol Empire (12th-13th century)",
        birth=1162,
        death=1227,
        occupation=["Khan of the Mongol Empire", "military commander", "lawgiver"],
        location="Mongolia",
        traits=["ambitious", "strategic", "ruthless", "pragmatic"],
        speech_style="Direct, commanding, uses steppe metaphors",
        knowledge_domain=["warfare", "diplomacy", "Mongol law", "steppe culture", "trade routes"],
        cutoff=1227,
        cultural_context="Nomadic steppe culture, unification of Mongol tribes, Silk Road trade",
        related_locations=["mongolia", "beijing", "samarkand", "silk road", "great wall"],
        related_topics=["mongol", "empire", "conquest", "silk road", "medieval", "steppe", "khan"],
    ),
    _character(
        name="Marie Curie",
        period="Early 20th century",
        birth=1867,
        death=1934,
        occupation=["physicist", "chemist", "Nobel laureate"],
        location="Paris, France",
        traits=["determined", "meticulous", "passionate", "resilient"],
        speech_style="Precise scientific language, occasionally passionate about discovery",
        knowledge_domain=["radioactivity", "physics", "chemistry", "X-rays", "scientific method"],
        cutoff=1934,
        cultural_context="Belle Époque, women in science, early nuclear physics, World War I field hospitals",
        related_locations=["paris", "france", "sorbonne", "warsaw", "poland"],
        related_topics=["radiation", "physics", "chemistry", "nobel", "science", "curie", "radioactivity"],
    ),
    _character(
        name="Ibn Battuta",
        period="Medieval Islamic World (14th century)",
        birth=1304,
        death=1369,
        occupation=["explorer", "scholar", "judge"],
        location="Tangier, Morocco",
        traits=["adventurous", "devout", "observant", "sociable"],
        speech_style="Vivid travel narratives, cultural comparisons, devout references",
        knowledge_domain=["geography", "Islamic law", "trade routes", "cultures of Africa/Asia"],
        cutoff=1369,
        cultural_context="Golden Age of Islamic civilization, Dar al-Islam connectivity, trade networks",
        related_locations=["morocco", "tangier", "mecca", "delhi", "istanbul", "mali", "china"],
        related_topics=["travel", "exploration", "islam", "medieval", "trade", "silk road", "africa", "asia"],
    ),
    _character(
        name="Nikola Tesla",
        period="Late 19th - Early 20th century",
        birth=1856,
        death=1943,
        occupation=["inventor", "electrical engineer", "physicist"],
        location="New York, USA",
        traits=["visionary", "eccentric", "brilliant", "solitary"],
        speech_style="Passionate about electricity and the future, visionary language",
        knowledge_domain=["electricity", "alternating current", "wireless transmission", "electromagnetism"],
        cutoff=1943,
        cultural_context="Second Industrial Revolution, War of Currents, early 20th century technology",
        related_locations=["new york", "niagara falls", "colorado springs", "serbia", "croatia"],
        related_topics=["electricity", "invention", "tesla", "current", "wireless", "energy", "engineering"],
    ),
    _character(
        name="Nefertiti",
        period="New Kingdom Egypt (14th century BCE)",
        birth=-1370,
        death=-1330,
        occupation=["Queen of Egypt", "religious reformer"],
        location="Amarna, Egypt",
        traits=["powerful", "devout", "regal", "reformist"],
        speech_style="Regal, poetic, references to Aten (sun disk)",
        knowledge_domain=["Egyptian religion", "Aten worship", "Egyptian art", "royal court"],
        cutoff=-1330,
        cultural_context="Amarna period, religious revolution from polytheism to Aten worship",
        related_locations=["egypt", "amarna", "thebes", "luxor", "karnak", "berlin museum"],
        related_topics=["egypt", "pharaoh", "nefertiti", "ancient", "aten", "amarna", "art"],
    ),
    _character(
        name="Galileo Galilei",
        period="Early Modern Europe (16th-17th century)",
        birth=1564,
        death=1642,
        occupation=["astronomer", "physicist", "mathematician", "philosopher"],
        location="Florence, Italy",
        traits=["inquisitive", "stubborn", "witty", "observant"],
        speech_style="Argumentative, uses observation-based reasoning, occasionally sardonic",
        knowledge_domain=["astronomy", "physics", "mathematics", "telescope observation", "mechanics"],
        cutoff=1642,
        cultural_context="Scientific Revolution, Counter-Reformation, conflict between science and Church",
        related_locations=["florence", "pisa", "rome", "venice", "italy", "vatican"],
        related_topics=["astronomy", "telescope", "galileo", "science", "physics", "renaissance", "church"],
    ),
    _character(
        name="Harriet Tubman",
        period="19th century America",
        birth=1822,
        death=1913,
        occupation=["abolitionist", "political activist", "Union spy"],
        location="Maryland / Auburn, New York, USA",
        traits=["courageous", "resourceful", "determined", "compassionate"],
        speech_style="Direct, uses Biblical references, passionate about freedom",
        knowledge_domain=["Underground Railroad", "slavery", "Civil War", "abolitionism"],
        cutoff=1913,
        cultural_context="Antebellum America, Underground Railroad, Civil War, Reconstruction",
        related_locations=["maryland", "new york", "philadelphia", "washington", "america"],
        related_topics=["slavery", "freedom", "underground railroad", "civil war", "abolitionist", "america"],
    ),
    _character(
        name="Zheng He",
        period="Ming Dynasty China (15th century)",
        birth=1371,
        death=1433,
        occupation=["admiral", "explorer", "diplomat"],
        location="Nanjing, China",
        traits=["ambitious", "diplomatic", "devout", "commanding"],
        speech_style="Formal, diplomatic, references to Chinese cosmology and maritime tradition",
        knowledge_domain=["navigation", "diplomacy", "Chinese maritime tradition", "trade", "Islam"],
        cutoff=1433,
        cultural_context="Ming Dynasty zenith, treasure voyages, Chinese maritime supremacy",
        related_locations=["china", "nanjing", "beijing", "southeast asia", "india", "africa", "forbidden city"],
        related_topics=["china", "exploration", "navigation", "ming", "trade", "maritime", "silk road"],
    ),
]


class HistoricalCharacterDatabase:
    """In-memory character library with relevance-based search.

    The database supports searching by location keywords, topic keywords,
    and time period.  Relevance scoring is a weighted combination of
    location match (40%), topic match (40%), and period match (20%).
    """

    def __init__(
        self, characters: list[HistoricalCharacter] | None = None
    ) -> None:
        self._characters = characters if characters is not None else list(_CHARACTERS)

    @property
    def characters(self) -> list[HistoricalCharacter]:
        return list(self._characters)

    async def find_relevant(
        self,
        *,
        location: str = "",
        topic: str = "",
        time_period: str = "",
        limit: int = 3,
    ) -> list[HistoricalCharacter]:
        """Find characters relevant to the given context.

        Returns up to *limit* characters sorted by descending relevance.
        """
        if not location and not topic and not time_period:
            return []

        scored: list[tuple[float, HistoricalCharacter]] = []

        location_words = set(re.findall(r"\b[a-z]{3,}\b", location.lower()))
        topic_words = set(re.findall(r"\b[a-z]{3,}\b", topic.lower()))
        period_words = set(re.findall(r"\b[a-z]{3,}\b", time_period.lower()))

        for char in self._characters:
            score = self._score_character(
                char, location_words, topic_words, period_words
            )
            if score > 0.0:
                scored.append((score, char))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [char for _, char in scored[:limit]]

    def _score_character(
        self,
        char: HistoricalCharacter,
        location_words: set[str],
        topic_words: set[str],
        period_words: set[str],
    ) -> float:
        """Score a character's relevance to the search terms.

        Weights: location=40%, topic=40%, period=20%.
        """
        # Location match (40%)
        char_locations = set()
        for loc in char.related_locations:
            char_locations.update(re.findall(r"\b[a-z]{3,}\b", loc.lower()))
        char_locations.update(re.findall(r"\b[a-z]{3,}\b", char.location.lower()))

        loc_overlap = location_words & char_locations
        loc_score = min(1.0, len(loc_overlap) / max(1, min(3, len(location_words)))) if location_words else 0.0

        # Topic match (40%)
        char_topics = set()
        for t in char.related_topics:
            char_topics.update(re.findall(r"\b[a-z]{3,}\b", t.lower()))
        for d in char.personality.knowledge_domain:
            char_topics.update(re.findall(r"\b[a-z]{3,}\b", d.lower()))
        for o in char.occupation:
            char_topics.update(re.findall(r"\b[a-z]{3,}\b", o.lower()))

        topic_overlap = topic_words & char_topics
        topic_score = min(1.0, len(topic_overlap) / max(1, min(3, len(topic_words)))) if topic_words else 0.0

        # Period match (20%)
        char_period_words = set(re.findall(r"\b[a-z]{3,}\b", char.historical_period.lower()))
        period_overlap = period_words & char_period_words
        period_score = min(1.0, len(period_overlap) / max(1, min(2, len(period_words)))) if period_words else 0.0

        return round(0.4 * loc_score + 0.4 * topic_score + 0.2 * period_score, 3)
