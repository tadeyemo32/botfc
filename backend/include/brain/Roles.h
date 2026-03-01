#ifndef ROLES_H
#define ROLES_H

#include <string>

namespace BHumanBot {

enum class PlayerRole { STRIKER, DEFENDER, BALANCED };

enum class SkillState {
  INIT,
  SEARCH,
  APPROACH,
  ALIGN,
  TACKLE,
  KICK,
  RECOVER,
  HALFTIME
};

inline std::string stateToString(SkillState state) {
  switch (state) {
  case SkillState::INIT:
    return "INIT";
  case SkillState::SEARCH:
    return "SEARCH";
  case SkillState::APPROACH:
    return "APPROACH";
  case SkillState::ALIGN:
    return "ALIGN";
  case SkillState::TACKLE:
    return "TACKLE";
  case SkillState::KICK:
    return "KICK";
  case SkillState::RECOVER:
    return "RECOVER";
  case SkillState::HALFTIME:
    return "HALFTIME";
  default:
    return "UNKNOWN";
  }
}
} // namespace BHumanBot

#endif // ROLES_H
