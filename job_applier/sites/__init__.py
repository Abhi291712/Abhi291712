from sites.linkedin import LinkedInSite
from sites.indeed import IndeedSite
from sites.dice import DiceSite
from sites.glassdoor import GlassdoorSite
from sites.ziprecruiter import ZipRecruiterSite
from sites.cvs import CVSSite
from sites.uhc import UHCSite

SITES = {
    "linkedin": LinkedInSite,
    "indeed": IndeedSite,
    "dice": DiceSite,
    "glassdoor": GlassdoorSite,
    "ziprecruiter": ZipRecruiterSite,
    "cvs": CVSSite,
    "uhc": UHCSite,
}
